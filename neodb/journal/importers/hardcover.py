import csv
import io
import logging
import os

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _
from markdownify import markdownify as md

from catalog.common import *
from catalog.models import *
from catalog.models.utils import detect_isbn_asin
from common.storage import media_exists, media_file_writer
from journal.models import *
from users.models import Task, TaskCancelled

from .storygraph import StoryGraphImporter, _parse_collect_date

logger = logging.getLogger(__name__)

SHELF_MAP = {
    "want to read": ShelfType.WISHLIST,
    "currently reading": ShelfType.PROGRESS,
    "paused": ShelfType.PROGRESS,
    "read": ShelfType.COMPLETE,
    # did-not-finish, which appears under either name
    "did not finish": ShelfType.DROPPED,
    "stopped": ShelfType.DROPPED,
}

MATCHED_EXTRA_COLUMNS = ["link", "match_source", "shelf", "collect_date"]

REQUIRED_COLUMNS = {"Title", "Author", "Status", "Hardcover Book ID"}


class HardcoverCancelled(TaskCancelled):
    """Raised by the worker when the view has flipped phase to 'cancelled'.

    Same mechanism as StoryGraphCancelled.
    """


def _credits(text: str) -> list[tuple[str, str]]:
    """Split the Author column into (name, role) pairs.

    A contributor other than an author has the role in parentheses after the
    name, e.g. "Lee Artist (Illustrator)"; the role is "" for a plain name.
    """
    result = []
    for part in text.split(","):
        name, role = part.strip(), ""
        if name.endswith(")") and "(" in name:
            name, _sep, role = name[:-1].rpartition("(")
            name, role = name.strip(), role.strip()
        if name:
            result.append((name, role))
    return result


def _search_author(text: str) -> str:
    """The name to search the catalogs with: the first credited author."""
    credits = _credits(text)
    for name, role in credits:
        if role.lower() in ("", "author"):
            return name
    return credits[0][0] if credits else ""


def _rating_grade(raw: str | None) -> int | None:
    """Hardcover stars run from 0.5 to 5 in halves, a NeoDB grade from 1 to 10."""
    try:
        return round(float((raw or "").strip()) * 2) or None
    except ValueError, OverflowError:
        return None


def privacy_to_visibility(privacy: str | None) -> int:
    """A book is never shown to more people than it was on Hardcover."""
    p = (privacy or "").strip().lower()
    if p == "public":
        return VisibilityType.Public
    if p.startswith("follower"):
        return VisibilityType.Follower_Only
    return VisibilityType.Private


def _site_url_prefix() -> str:
    return settings.SITE_INFO["site_url"].rstrip("/")


class HardcoverImporter(Task):
    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    TaskQueue = "import"
    DefaultMetadata = {
        "phase": "matching",
        "file": None,
        "matched_file": None,
        "filename_hint": None,
        "total": 0,
        "processed": 0,
        "matched_local": 0,
        "matched_external": 0,
        "unmatched": 0,
        "imported": 0,
        "skipped": 0,
        "failed": 0,
        "failed_items": [],
        "had_link_column": False,
    }

    @classmethod
    def validate_file(cls, uploaded_file) -> bool:
        try:
            head = uploaded_file.read(4096).decode("utf-8-sig", errors="ignore")
            uploaded_file.seek(0)
            header = next(csv.reader(io.StringIO(head.splitlines()[0])))
            return REQUIRED_COLUMNS.issubset(h.strip() for h in header)
        except Exception:
            return False

    PROGRESS_SAVE_EVERY = 25  # flush task row at most every N processed rows

    def _raise_if_cancelled(self) -> None:
        """Honour user-initiated cancellation written by the view."""
        fresh = (
            type(self)
            .objects.filter(pk=self.pk)
            .values_list("metadata", flat=True)
            .first()
        )
        if fresh and fresh.get("phase") == "cancelled":
            raise HardcoverCancelled()

    def progress(self, *, force: bool = False, **delta) -> None:
        for k, v in delta.items():
            self.metadata[k] = self.metadata.get(k, 0) + v
        phase = self.metadata.get("phase", "")
        total = self.metadata.get("total", 0)
        done = self.metadata.get("processed", 0)
        if phase == "matching":
            self.message = _(
                "Matching: {done}/{total} — {local} local, {ext} external, {none} unmatched"
            ).format(
                done=done,
                total=total,
                local=self.metadata.get("matched_local", 0),
                ext=self.metadata.get("matched_external", 0),
                none=self.metadata.get("unmatched", 0),
            )
        else:
            self.message = _(
                "Importing: {imported} imported, {skipped} skipped, {failed} failed"
            ).format(
                imported=self.metadata.get("imported", 0),
                skipped=self.metadata.get("skipped", 0),
                failed=self.metadata.get("failed", 0),
            )
        if force or done % self.PROGRESS_SAVE_EVERY == 0 or done == total:
            self._raise_if_cancelled()
            self.save(update_fields=["metadata", "message"])

    def run(self) -> None:
        phase = self.metadata.get("phase", "matching")
        if phase == "matching":
            self._run_matching()
        elif phase == "importing":
            self._run_import()
        else:
            logger.warning(
                f"HardcoverImporter run() called in unexpected phase: {phase}"
            )

    # ---- Phase 1: matching ----

    def _run_matching(self) -> None:
        in_path = self.local_path()
        out_key = self._derive_matched_path(self.metadata["file"])
        with open(in_path, encoding="utf-8-sig", newline="") as fin:
            reader = csv.DictReader(fin)
            fieldnames = [h.strip() for h in reader.fieldnames or []]
            extra = [c for c in MATCHED_EXTRA_COLUMNS if c not in fieldnames]
            # k is None for extra cells in ragged rows; drop them
            rows = [{k.strip(): v for k, v in raw.items() if k} for raw in reader]
        self.metadata["total"] = len(rows)
        self.metadata["matched_file"] = out_key
        self._raise_if_cancelled()
        self.save(update_fields=["metadata"])

        with media_file_writer(out_key) as out_path:
            with open(out_path, "w", encoding="utf-8", newline="") as fout:
                writer = csv.DictWriter(fout, fieldnames=fieldnames + extra)
                writer.writeheader()
                for row in rows:
                    self._match_row(row)
                    writer.writerow(row)

        self.metadata["phase"] = "preview"
        self.message = _(
            "Matching complete: {local} local, {ext} external, {none} unmatched"
        ).format(
            local=self.metadata.get("matched_local", 0),
            ext=self.metadata.get("matched_external", 0),
            none=self.metadata.get("unmatched", 0),
        )
        self._raise_if_cancelled()
        self.save(update_fields=["metadata", "message"])

    def _match_row(self, row: dict) -> None:
        # unmapped statuses default to skip
        shelf = SHELF_MAP.get((row.get("Status") or "").strip().lower())
        row.setdefault("shelf", shelf.value if shelf else "")

        if not (row.get("collect_date") or "").strip():
            match shelf:
                case ShelfType.COMPLETE:
                    dt = _parse_collect_date(row.get("Date Finished"))
                case ShelfType.PROGRESS | ShelfType.DROPPED:
                    dt = _parse_collect_date(row.get("Date Started"))
                case _:
                    dt = None
            dt = dt or _parse_collect_date(row.get("Date Added"))
            row["collect_date"] = dt.strftime("%Y-%m-%d") if dt else ""

        # already populated link (round-trip)
        if (row.get("link") or "").strip():
            row.setdefault("match_source", "preset")
            self.progress(processed=1)
            return

        match = self._match(row)
        if match:
            row["link"], row["match_source"] = match
            if row["match_source"] == "local":
                self.progress(processed=1, matched_local=1)
            else:
                self.progress(processed=1, matched_external=1)
            return

        row["link"] = ""
        row["match_source"] = "none"
        self.progress(processed=1, unmatched=1)

    @classmethod
    def _match(cls, row: dict) -> tuple[str, str] | None:
        """Return (link, match_source) for a row, or None if nothing matched.

        Hardcover IDs are of no use without a Hardcover catalog site, so like
        StoryGraph a row is matched by ISBN/ASIN, then by title and author,
        with StoryGraph's lookups.
        """
        ids: list[tuple[IdType, str]] = []
        for column in ("ISBN 13", "ISBN 10", "ASIN"):
            id_type, id_value = detect_isbn_asin((row.get(column) or "").strip())
            if id_type and id_value and (id_type, id_value) not in ids:
                ids.append((id_type, id_value))

        # every identifier in the local DB first (no network)
        for id_type, id_value in ids:
            er = ExternalResource.objects.filter(
                id_type=id_type, id_value=id_value
            ).first()
            if er and er.item:
                return er.item.url, "local"

        for id_type, id_value in ids:
            if id_type != IdType.ISBN:
                continue
            url = StoryGraphImporter._match_by_isbn_google_books(id_value)
            if url:
                return url, "googlebooks"
            url = StoryGraphImporter._match_by_isbn_openlibrary(id_value)
            if url:
                return url, "openlibrary"

        title = (row.get("Title") or "").strip()
        if title:
            author = _search_author(row.get("Author") or "")
            item = StoryGraphImporter._match_via_local_index(title, author)
            if item:
                return item.url, "local"
            url = StoryGraphImporter._match_via_google_books(title, author)
            if url:
                return url, "googlebooks"
            url = StoryGraphImporter._match_via_openlibrary_search(title, author)
            if url:
                return url, "openlibrary"

        return None

    # ---- Phase 2: import ----

    def _run_import(self) -> None:
        path = self.metadata.get("matched_file")
        if not path or not media_exists(path):
            self.message = _("Matched file missing; cannot import.")
            self.save(update_fields=["message"])
            return
        with open(
            self.local_path("matched_file"), encoding="utf-8-sig", newline=""
        ) as f:
            reader = csv.DictReader(f)
            rows = [{k.strip(): v for k, v in r.items() if k} for r in reader]
        self.metadata["total"] = len(rows)
        self.metadata["processed"] = 0
        self._raise_if_cancelled()
        self.save(update_fields=["metadata"])
        owner = self.user.identity
        for row in rows:
            try:
                self._import_row(row, owner)
            except HardcoverCancelled:
                raise
            except Exception as e:
                logger.exception(f"Hardcover row import failed: {e}")
                self._fail_row(row)

        self.metadata["phase"] = "done"
        self.message = _(
            "Import complete: {imported} imported, {skipped} skipped, {failed} failed"
        ).format(
            imported=self.metadata.get("imported", 0),
            skipped=self.metadata.get("skipped", 0),
            failed=self.metadata.get("failed", 0),
        )
        self._raise_if_cancelled()
        self.save(update_fields=["metadata", "message"])

    def _fail_row(self, row: dict) -> None:
        self.progress(processed=1, failed=1)
        label = (row.get("Title") or "").strip() or (row.get("link") or "").strip()
        if label:
            self.metadata["failed_items"].append(label)
            self._raise_if_cancelled()
            self.save(update_fields=["metadata"])

    def _import_row(self, row: dict, owner) -> None:
        link = (row.get("link") or "").strip()
        shelf_raw = (row.get("shelf") or "").strip()

        if not link or not shelf_raw:
            # no match picked, or explicit "skip" from user
            self.progress(processed=1, skipped=1)
            return
        try:
            shelf_type = ShelfType(shelf_raw)
        except ValueError:
            self.progress(processed=1, skipped=1)
            return

        item = self._resolve_link(link)
        if not item:
            self._fail_row(row)
            return

        visibility = privacy_to_visibility(row.get("Privacy"))

        rating = _rating_grade(row.get("Rating"))

        # Review text (may contain HTML)
        review_html = (row.get("Review") or "").strip()
        comment: str | None = None
        long_review: str | None = None
        if review_html:
            has_html = "<" in review_html
            review_text = md(review_html) if has_html else review_html
            if not has_html and len(review_text) < 360:
                comment = review_text
            else:
                long_review = review_text

        dt = _parse_collect_date(row.get("collect_date")) or timezone.now()

        mark = Mark(owner, item)
        is_downgrade = (
            mark.shelf_type == ShelfType.COMPLETE and shelf_type != ShelfType.COMPLETE
        ) or (
            mark.shelf_type in [ShelfType.PROGRESS, ShelfType.DROPPED]
            and shelf_type == ShelfType.WISHLIST
        )
        if is_downgrade:
            self.progress(processed=1, skipped=1)
            return
        # a fresh export may only change privacy or rating, and a book made
        # private on Hardcover must not stay public here
        if mark.shelf_type == shelf_type and mark.visibility == visibility:
            existing_review = Review.objects.filter(owner=owner, item=item).first()
            review_body = existing_review.body if existing_review else None
            if (
                comment == mark.comment_text
                and long_review == review_body
                and rating in (None, mark.rating_grade)
                and (not existing_review or existing_review.visibility == visibility)
            ):
                self.progress(processed=1, skipped=1)
                return

        mark.update(
            shelf_type,
            comment,
            rating,
            visibility=visibility,
            created_time=dt,
        )
        if long_review:
            item_title = item.display_title or (row.get("Title") or "").strip()
            Review.update_item_review(
                item,
                owner,
                _("a review of {item_title}").format(item_title=item_title),
                long_review,
                visibility,
                dt,
            )
        self.progress(processed=1, imported=1)

    def _resolve_link(self, url: str) -> Item | None:
        site_url = _site_url_prefix() + "/"
        if url.startswith("/") or url.startswith(site_url):
            item = Item.get_by_url(url, resolve_merge=True)
            if item and not item.is_deleted:
                return item
            return None
        site = SiteManager.get_site_by_url(url, detect_redirection=False)
        if not site:
            return None
        item = site.get_item()
        if item:
            return item
        try:
            site.get_resource_ready()
        except Exception as e:
            logger.warning(f"Hardcover remote fetch failed for {url}: {e}")
            return None
        return site.get_item()

    # ---- helpers ----

    @staticmethod
    def _derive_matched_path(in_path: str) -> str:
        """The matched file's key, beside the uploaded one it derives from."""
        stem, _ext = os.path.splitext(in_path)
        return f"{stem}-matched.csv"
