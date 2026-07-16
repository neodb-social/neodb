import csv
import datetime
import os
import re
from urllib.parse import quote_plus

from django.conf import settings
from django.utils import timezone
from django.utils.timezone import make_aware
from django.utils.translation import gettext as _
from loguru import logger
from markdownify import markdownify as md

from catalog.common import *
from catalog.models import *
from catalog.models.utils import detect_isbn_asin
from catalog.search.index import CatalogIndex, CatalogQueryParser
from journal.models import *
from users.models import Task

SHELF_MAP = {
    "read": ShelfType.COMPLETE,
    "to-read": ShelfType.WISHLIST,
    "currently-reading": ShelfType.PROGRESS,
    "did-not-finish": ShelfType.DROPPED,
}

MATCHED_EXTRA_COLUMNS = ["link", "match_source", "shelf", "collect_date"]

# StoryGraph puts its own book UUID in the ISBN/UID column when the edition has no ISBN
_RE_STORYGRAPH_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class StoryGraphCancelled(Exception):
    """Raised by the worker when the view has flipped phase to 'cancelled'.

    Same mechanism as RymCancelled: the cancel view writes ``phase=cancelled``
    directly to the DB, so the worker re-reads it before every save and bails.
    """


def _first_author(authors: str) -> str:
    return authors.split(",")[0].strip() if authors else ""


def _titles_match(a: str, b: str) -> bool:
    """Accept if either title contains the other (case-insensitive)."""
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    return bool(a) and bool(b) and (a in b or b in a)


def _escape_quotes(s: str) -> str:
    return s.replace('"', '\\"')


def _parse_collect_date(raw: str | None) -> datetime.datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return make_aware(datetime.datetime.strptime(raw, fmt).replace(hour=22))
        except ValueError:
            continue
    return None


def _site_url_prefix() -> str:
    return settings.SITE_INFO["site_url"].rstrip("/")


class StoryGraphImporter(Task):
    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    TaskQueue = "import"
    DefaultMetadata = {
        "phase": "matching",
        "visibility": 0,
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
            first_line = uploaded_file.read(200).decode("utf-8", errors="ignore")
            uploaded_file.seek(0)
            return first_line.startswith("Title,Authors,Contributors,ISBN/UID,")
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
            raise StoryGraphCancelled()

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
                f"StoryGraphImporter run() called in unexpected phase: {phase}"
            )

    # ---- Phase 1: matching ----

    def _run_matching(self) -> None:
        in_path = self.metadata["file"]
        out_path = self._derive_matched_path(in_path)
        with open(in_path, encoding="utf-8-sig", newline="") as fin:
            reader = csv.DictReader(fin)
            fieldnames = [h.strip() for h in reader.fieldnames or []]
            extra = [c for c in MATCHED_EXTRA_COLUMNS if c not in fieldnames]
            # k is None for extra cells in ragged rows; drop them
            rows = [{k.strip(): v for k, v in raw.items() if k} for raw in reader]
        self.metadata["total"] = len(rows)
        self.metadata["matched_file"] = out_path
        self._raise_if_cancelled()
        self.save(update_fields=["metadata"])

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
        title = (row.get("Title") or "").strip()
        authors = (row.get("Authors") or "").strip()
        isbn_uid = (row.get("ISBN/UID") or "").strip()

        # default shelf from Read Status; unmapped statuses default to skip
        shelf = SHELF_MAP.get((row.get("Read Status") or "").strip())
        row.setdefault("shelf", shelf.value if shelf else "")

        # default collect date: last read date for completed books, else date added
        if not (row.get("collect_date") or "").strip():
            if shelf == ShelfType.COMPLETE and row.get("Last Date Read"):
                raw_date = row["Last Date Read"]
            else:
                raw_date = row.get("Date Added", "")
            dt = _parse_collect_date(raw_date)
            row["collect_date"] = dt.strftime("%Y-%m-%d") if dt else ""

        # already populated link (round-trip)
        if (row.get("link") or "").strip():
            row.setdefault("match_source", "preset")
            self.progress(processed=1)
            return

        match = self._match(isbn_uid, title, authors)
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
    def _match(cls, isbn_uid: str, title: str, authors: str) -> tuple[str, str] | None:
        """Return (link, match_source) for a row, or None if nothing matched."""
        id_type, id_value = detect_isbn_asin(isbn_uid)
        sg_uid = isbn_uid.lower() if _RE_STORYGRAPH_UUID.match(isbn_uid.lower()) else ""

        # ISBN/ASIN or StoryGraph UUID lookup in local DB (no network)
        if id_type and id_value:
            er = ExternalResource.objects.filter(
                id_type=id_type, id_value=id_value
            ).first()
            if er and er.item:
                return er.item.url, "local"
        elif sg_uid:
            er = ExternalResource.objects.filter(
                id_type=IdType.StoryGraph, id_value=sg_uid
            ).first()
            if er and er.item:
                return er.item.url, "local"

        # exact edition by ISBN from Google Books, then OpenLibrary
        if id_type == IdType.ISBN and id_value:
            url = cls._match_by_isbn_google_books(id_value)
            if url:
                return url, "googlebooks"
            url = cls._match_by_isbn_openlibrary(id_value)
            if url:
                return url, "openlibrary"

        # StoryGraph book page; importing it needs a JS-rendering scrape provider
        if sg_uid and settings.DOWNLOADER_PROVIDERS:
            return f"https://app.thestorygraph.com/books/{sg_uid}", "storygraph"

        # title + author matching, local catalog index first then external
        if title:
            item = cls._match_via_local_index(title, authors)
            if item:
                return item.url, "local"
            url = cls._match_via_google_books(title, authors)
            if url:
                return url, "googlebooks"
            url = cls._match_via_openlibrary_search(title, authors)
            if url:
                return url, "openlibrary"

        return None

    @classmethod
    def _match_by_isbn_google_books(cls, isbn: str) -> str | None:
        api_url = f"https://www.googleapis.com/books/v1/volumes?country=us&q=isbn:{isbn}&maxResults=3"
        try:
            j = BasicDownloader(api_url).download().json()
            for book in j.get("items", []):
                identifiers = [
                    i.get("identifier")
                    for i in book.get("volumeInfo", {}).get("industryIdentifiers", [])
                ]
                if isbn not in identifiers or "id" not in book:
                    continue
                return "https://books.google.com/books?id=" + book["id"]
        except Exception as e:
            logger.warning(f"Google Books ISBN lookup failed for {isbn}: {e}")
        return None

    @classmethod
    def _match_by_isbn_openlibrary(cls, isbn: str) -> str | None:
        api_url = f"https://openlibrary.org/isbn/{isbn}.json"
        try:
            j = BasicDownloader(api_url).download().json()
            key = j.get("key", "")  # "/books/OL...M"
            if key.startswith("/books/"):
                return "https://openlibrary.org" + key
        except Exception as e:
            logger.warning(f"OpenLibrary ISBN lookup failed for {isbn}: {e}")
        return None

    @classmethod
    def _match_via_local_index(cls, title: str, authors: str) -> Item | None:
        first_author = _first_author(authors)
        q = f'"{_escape_quotes(title)}"'
        if first_author:
            q += f' people:"{_escape_quotes(first_author)}"'
        q += " category:book"
        try:
            parser = CatalogQueryParser(q, page=1, page_size=5)
            hits = list(CatalogIndex.instance().search(parser).items)
        except Exception as e:
            logger.warning(f"StoryGraph local index search failed: {e}")
            return None
        author_lc = first_author.lower()
        for it in hits:
            if not isinstance(it, Edition):
                continue
            titles = [(t.get("text") or "") for t in it.localized_title or []]
            titles.append(it.title or "")
            title_ok = any(_titles_match(title, t) for t in titles)
            author_names = it.credit_names_by_role("author") + (it.author or [])
            author_ok = (not author_lc) or any(
                author_lc in (a or "").lower() or (a or "").lower() in author_lc
                for a in author_names
            )
            if title_ok and author_ok:
                return it
        return None

    @classmethod
    def _match_via_google_books(cls, title: str, authors: str) -> str | None:
        # Build query: intitle + inauthor (first author only for precision)
        q = f"intitle:{quote_plus(title)}"
        first_author = _first_author(authors)
        if first_author:
            q += f"+inauthor:{quote_plus(first_author)}"
        api_url = (
            f"https://www.googleapis.com/books/v1/volumes?country=us&q={q}&maxResults=3"
        )
        try:
            j = BasicDownloader(api_url).download().json()
            for book in j.get("items", []):
                result_title = book.get("volumeInfo", {}).get("title", "")
                if not _titles_match(title, result_title) or "id" not in book:
                    continue
                return "https://books.google.com/books?id=" + book["id"]
        except Exception as e:
            logger.warning(f"Google Books search failed for '{title}': {e}")
        return None

    @classmethod
    def _match_via_openlibrary_search(cls, title: str, authors: str) -> str | None:
        first_author = _first_author(authors)
        api_url = (
            f"https://openlibrary.org/search.json?title={quote_plus(title)}"
            + (f"&author={quote_plus(first_author)}" if first_author else "")
            + "&limit=3&fields=key,title,editions,editions.key,editions.title"
        )
        try:
            j = BasicDownloader(api_url).download().json()
            for work in j.get("docs", []):
                editions = work.get("editions", {}).get("docs", [])
                if not editions:
                    continue
                edition = editions[0]
                # edition title is closer to what the user shelved than work title
                if not _titles_match(
                    title, edition.get("title", "")
                ) and not _titles_match(title, work.get("title", "")):
                    continue
                key = edition.get("key", "")  # "/books/OL...M"
                if not key.startswith("/books/"):
                    continue
                return "https://openlibrary.org" + key
        except Exception as e:
            logger.warning(f"OpenLibrary search failed for '{title}': {e}")
        return None

    # ---- Phase 2: import ----

    def _run_import(self) -> None:
        path = self.metadata.get("matched_file")
        if not path or not os.path.exists(path):
            self.message = _("Matched file missing; cannot import.")
            self.save(update_fields=["message"])
            return
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = [{k.strip(): v for k, v in r.items() if k} for r in reader]
        self.metadata["total"] = len(rows)
        self.metadata["processed"] = 0
        self._raise_if_cancelled()
        self.save(update_fields=["metadata"])
        visibility = int(self.metadata.get("visibility", 0))
        owner = self.user.identity
        for row in rows:
            try:
                self._import_row(row, owner, visibility)
            except StoryGraphCancelled:
                raise
            except Exception as e:
                logger.exception(f"StoryGraph row import failed: {e}")
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

    def _import_row(self, row: dict, owner, visibility: int) -> None:
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

        # Rating: StoryGraph uses 0.5–5.0 half-stars; NeoDB uses 1–10 integers
        rating_raw = (row.get("Star Rating") or "").strip()
        rating: int | None = None
        if rating_raw:
            try:
                rating = round(float(rating_raw) * 2) or None
            except ValueError:
                pass

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
        if mark.shelf_type == shelf_type:
            existing_review = Review.objects.filter(owner=owner, item=item).first()
            review_body = existing_review.body if existing_review else None
            if comment == mark.comment_text and long_review == review_body:
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
            logger.warning(f"StoryGraph remote fetch failed for {url}: {e}")
            return None
        return site.get_item()

    # ---- helpers ----

    @staticmethod
    def _derive_matched_path(in_path: str) -> str:
        base = os.path.basename(in_path)
        stem, _ext = os.path.splitext(base)
        out_dir = os.path.dirname(in_path)
        return os.path.join(out_dir, f"{stem}-matched.csv")
