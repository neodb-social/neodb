import asyncio
import csv
import datetime
import fcntl
import os
import re
import tempfile
import time

from django.conf import settings
from django.utils import timezone
from django.utils.timezone import make_aware
from django.utils.translation import gettext as _
from loguru import logger

from catalog.common import SiteManager
from catalog.models import Album, Item
from catalog.search.index import CatalogIndex, CatalogQueryParser
from catalog.sites.musicbrainz import MusicBrainzRelease
from catalog.sites.spotify import Spotify
from common.models import SiteConfig
from journal.models import Mark, Review, ShelfType
from users.models import Task

RYM_HEADER_PREFIX = "RYM Album,"
OWNERSHIP_TO_SHELF = {
    "o": ShelfType.COMPLETE,
    "w": ShelfType.WISHLIST,
    "u": ShelfType.DROPPED,  # RYM "used to own"
}
MATCHED_EXTRA_COLUMNS = ["link", "match_source", "shelf", "collect_date", "notes"]

_BBCODE_PATTERNS = [
    (re.compile(r"\[b\](.*?)\[/b\]", re.DOTALL | re.IGNORECASE), r"**\1**"),
    (re.compile(r"\[i\](.*?)\[/i\]", re.DOTALL | re.IGNORECASE), r"*\1*"),
    (re.compile(r"\[s\](.*?)\[/s\]", re.DOTALL | re.IGNORECASE), r"~~\1~~"),
    (re.compile(r"\[u\](.*?)\[/u\]", re.DOTALL | re.IGNORECASE), r"\1"),
    (
        re.compile(r"\[url=([^\]]+)\](.*?)\[/url\]", re.DOTALL | re.IGNORECASE),
        r"[\2](\1)",
    ),
    (re.compile(r"\[url\](.*?)\[/url\]", re.DOTALL | re.IGNORECASE), r"\1"),
    (re.compile(r"\[/?(?:Artist|Album|Release)\d+\]", re.IGNORECASE), r""),
]


def _bbcode_to_md(text: str) -> str:
    """Convert the subset of BBCode that RateYourMusic emits to Markdown."""
    if not text:
        return ""
    out = text
    for pat, repl in _BBCODE_PATTERNS:
        out = pat.sub(repl, out)
    return out.strip()


def _row_artist(row: dict) -> str:
    """Concatenate First/Last (preferring localized variants when present)."""
    first_loc = (row.get("First Name localized") or "").strip()
    last_loc = (row.get("Last Name localized") or "").strip()
    if first_loc or last_loc:
        return f"{first_loc} {last_loc}".strip()
    return f"{(row.get('First Name') or '').strip()} {(row.get('Last Name') or '').strip()}".strip()


def _run_async(coro, loop: asyncio.AbstractEventLoop | None = None):
    """Run an async coroutine synchronously.

    When *loop* is provided it's reused (cheaper for tight match loops);
    otherwise a one-shot loop is created and closed for the call.
    """
    if loop is not None:
        return loop.run_until_complete(coro)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _parse_collect_date(raw: str | None) -> datetime.datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return make_aware(datetime.datetime.strptime(raw, fmt).replace(hour=22))
        except ValueError:
            continue
    return None


def _default_collect_date(purchase_date_raw: str | None) -> datetime.datetime:
    dt = _parse_collect_date(purchase_date_raw)
    if dt:
        return dt
    return timezone.now() - datetime.timedelta(days=7)


def _site_url_prefix() -> str:
    return settings.SITE_INFO["site_url"].rstrip("/")


class RymImporter(Task):
    class Meta:
        app_label = "journal"

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
        "failed_urls": [],
        "had_link_column": False,
    }

    @classmethod
    def validate_file(cls, uploaded_file) -> bool:
        try:
            first_line = uploaded_file.read(200).decode("utf-8", errors="ignore")
            uploaded_file.seek(0)
            return first_line.lstrip("﻿").startswith(RYM_HEADER_PREFIX)
        except Exception:
            return False

    PROGRESS_SAVE_EVERY = 25  # flush task row at most every N processed rows

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
        # Avoid hammering the DB with one save per CSV row.
        if force or done % self.PROGRESS_SAVE_EVERY == 0 or done == total:
            self.save(update_fields=["metadata", "message"])

    def run(self) -> None:
        phase = self.metadata.get("phase", "matching")
        if phase == "matching":
            self._run_matching()
        elif phase == "importing":
            self._run_import()
        else:
            logger.warning(f"RymImporter run() called in unexpected phase: {phase}")

    # ---- Phase 1: matching ----

    def _run_matching(self) -> None:
        in_path = self.metadata["file"]
        out_path = self._derive_matched_path(in_path)
        with open(in_path, encoding="utf-8-sig", newline="") as fin:
            reader = csv.DictReader(fin)
            raw_fieldnames = list(reader.fieldnames or [])
            fieldnames = [h.strip() for h in raw_fieldnames]
            extra = [c for c in MATCHED_EXTRA_COLUMNS if c not in fieldnames]
            rows = []
            for raw_row in reader:
                # normalize header whitespace (RYM exports have inconsistent spaces)
                rows.append({k.strip(): v for k, v in raw_row.items()})
        self.metadata["total"] = len(rows)
        self.metadata["matched_file"] = out_path
        self.save(update_fields=["metadata"])

        # One asyncio loop reused for all external lookups in this phase —
        # avoids the cost of creating/closing a loop per row.
        self._match_loop = asyncio.new_event_loop()
        try:
            with open(out_path, "w", encoding="utf-8", newline="") as fout:
                writer = csv.DictWriter(fout, fieldnames=fieldnames + extra)
                writer.writeheader()
                for row in rows:
                    self._match_row(row)
                    writer.writerow(row)
        finally:
            self._match_loop.close()
            self._match_loop = None

        self.metadata["phase"] = "preview"
        self.message = _(
            "Matching complete: {local} local, {ext} external, {none} unmatched"
        ).format(
            local=self.metadata.get("matched_local", 0),
            ext=self.metadata.get("matched_external", 0),
            none=self.metadata.get("unmatched", 0),
        )
        self.save(update_fields=["metadata", "message"])

    def _match_row(self, row: dict) -> None:
        title = (row.get("Title") or "").strip()
        artist = _row_artist(row)
        year = (row.get("Release_Date") or "").strip() or None

        # default shelf from RYM Ownership; anything not in OWNERSHIP_TO_SHELF
        # (e.g. RYM 'n' = never owned) falls back to COMPLETE so no row defaults
        # to skip — the user can still flip individual rows in the preview UI.
        ownership = (row.get("Ownership") or "").strip().lower()
        shelf = OWNERSHIP_TO_SHELF.get(ownership, ShelfType.COMPLETE)
        row.setdefault("shelf", shelf.value)

        # default collect date
        if not row.get("collect_date"):
            cd = _default_collect_date(row.get("Purchase Date"))
            row["collect_date"] = cd.strftime("%Y-%m-%d")

        # already populated link (round-trip)
        if (row.get("link") or "").strip():
            row.setdefault("match_source", "preset")
            self.progress(processed=1)
            return

        item = self._local_match(title, artist, year)
        if item:
            row["link"] = item.url
            row["match_source"] = "local"
            self.progress(processed=1, matched_local=1)
            return

        ext_url = self._external_match(title, artist, year)
        if ext_url:
            row["link"] = ext_url[0]
            row["match_source"] = ext_url[1]
            self.progress(processed=1, matched_external=1)
            return

        row["link"] = ""
        row["match_source"] = "none"
        self.progress(processed=1, unmatched=1)

    def _local_match(self, title: str, artist: str, year: str | None) -> Item | None:
        if not title:
            return None
        q = f'"{_escape_quotes(title)}"'
        if artist:
            q += f' people:"{_escape_quotes(artist)}"'
        if year:
            q += f" year:{year}"
        q += " category:music"
        try:
            parser = CatalogQueryParser(q, page=1, page_size=5)
            hits = list(CatalogIndex.instance().search(parser).items)
        except Exception as e:
            logger.warning(f"RYM local index search failed: {e}")
            hits = []
        title_lc = title.lower()
        artist_lc = artist.lower()
        for it in hits:
            if not isinstance(it, Album):
                continue
            titles = [(t.get("text") or "").lower() for t in it.localized_title or []]
            titles.append((it.title or "").lower())
            title_ok = any(
                title_lc == t or title_lc in t or t in title_lc for t in titles if t
            )
            artist_ok = (not artist) or any(
                artist_lc in (a or "").lower() or (a or "").lower() in artist_lc
                for a in (it.artist or [])
            )
            if title_ok and artist_ok:
                return it
        return None

    def _external_match(
        self, title: str, artist: str, year: str | None
    ) -> tuple[str, str] | None:
        if not title:
            return None
        loop = getattr(self, "_match_loop", None)
        # MusicBrainz guideline: max 1 request/sec per IP
        time.sleep(1.05)
        try:
            mb = _run_async(
                MusicBrainzRelease.search_by_fields(title, artist, year), loop
            )
        except Exception as e:
            logger.warning(f"RYM MusicBrainz search failed: {e}")
            mb = []
        if mb:
            return mb[0].source_url, "musicbrainz"
        if SiteConfig.system.spotify_api_key:
            try:
                sp = _run_async(Spotify.search_by_fields(title, artist, year), loop)
            except Exception as e:
                logger.warning(f"RYM Spotify search failed: {e}")
                sp = []
            if sp:
                return sp[0].source_url, "spotify"
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
            rows = [{k.strip(): v for k, v in r.items()} for r in reader]
        self.metadata["total"] = len(rows)
        self.metadata["processed"] = 0
        self.save(update_fields=["metadata"])
        visibility = int(self.metadata.get("visibility", 0))
        owner = self.user.identity
        for row in rows:
            try:
                self._import_row(row, owner, visibility)
            except Exception as e:
                logger.exception(f"RYM row import failed: {e}")
                self.progress(processed=1, failed=1)
                url = (row.get("link") or "").strip()
                if url:
                    self.metadata["failed_urls"].append(url)
                    self.save(update_fields=["metadata"])

        self.metadata["phase"] = "done"
        self.message = _(
            "Import complete: {imported} imported, {skipped} skipped, {failed} failed"
        ).format(
            imported=self.metadata.get("imported", 0),
            skipped=self.metadata.get("skipped", 0),
            failed=self.metadata.get("failed", 0),
        )
        self.save(update_fields=["metadata", "message"])

    def _import_row(self, row: dict, owner, visibility: int) -> None:
        link = (row.get("link") or "").strip()
        shelf_raw = (row.get("shelf") or "").strip()
        rating_raw = int_or_zero(row.get("Rating"))
        review_raw = (row.get("Review") or "").strip()
        title = (row.get("Title") or "").strip()

        if not link:
            # nothing to do — user didn't pick a match
            self.progress(processed=1, skipped=1)
            return
        if not shelf_raw:
            # rating/review but explicit "skip" from user
            self.progress(processed=1, skipped=1)
            return
        try:
            shelf_type = ShelfType(shelf_raw)
        except ValueError:
            self.progress(processed=1, skipped=1)
            return

        item = self._resolve_link(link)
        if not item:
            self.progress(processed=1, failed=1)
            self.metadata["failed_urls"].append(link)
            self.save(update_fields=["metadata"])
            return

        review_md = _bbcode_to_md(review_raw)
        comment: str | None = None
        long_review: str | None = None
        if review_md:
            if "\n" not in review_md and len(review_md) < 360:
                comment = review_md
            else:
                long_review = review_md

        rating = rating_raw if rating_raw > 0 else None
        collect_dt = _parse_collect_date(row.get("collect_date")) or (
            timezone.now() - datetime.timedelta(days=7)
        )

        mark = Mark(owner, item)
        mark.update(
            shelf_type,
            comment,
            rating,
            visibility=visibility,
            created_time=collect_dt,
        )
        if long_review:
            item_title = item.display_title or title
            Review.update_item_review(
                item,
                owner,
                (row.get("Review Title") or "").strip()
                or _("a review of {item_title}").format(item_title=item_title),
                long_review,
                visibility,
                collect_dt,
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
            logger.warning(f"RYM remote fetch failed for {url}: {e}")
            return None
        return site.get_item()

    # ---- helpers ----

    @staticmethod
    def _derive_matched_path(in_path: str) -> str:
        base = os.path.basename(in_path)
        stem, _ext = os.path.splitext(base)
        out_dir = os.path.dirname(in_path)
        return os.path.join(out_dir, f"{stem}-matched.csv")


def _escape_quotes(s: str) -> str:
    return s.replace('"', '\\"')


def int_or_zero(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def update_row_in_matched_file(path: str, index: int, updates: dict) -> dict | None:
    """Apply ``updates`` to the ``index``-th data row of ``path`` (atomic rewrite).

    Wraps the read-modify-write in an exclusive ``fcntl.flock`` on a sibling
    ``<path>.lock`` so concurrent HTMX saves from the same user can't clobber
    each other.

    Returns the updated row, or None if the index is out of range.
    """
    lock_path = path + ".lock"
    with open(lock_path, "a+") as lock_fp:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
        return _update_row_locked(path, index, updates)


def _update_row_locked(path: str, index: int, updates: dict) -> dict | None:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if index < 0 or index >= len(rows):
        return None
    rows[index].update({k: v for k, v in updates.items() if k in fieldnames})
    fd, tmp = tempfile.mkstemp(
        suffix=".csv", dir=os.path.dirname(path) or None, text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fout:
            writer = csv.DictWriter(fout, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise
    return rows[index]
