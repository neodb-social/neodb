import csv
from datetime import datetime
from urllib.parse import quote_plus

from django.utils import timezone
from django.utils.timezone import make_aware
from django.utils.translation import gettext as _
from loguru import logger
from markdownify import markdownify as md

from catalog.common import *
from catalog.models import *
from catalog.models.utils import detect_isbn_asin
from journal.models import *
from users.models import Task

SHELF_MAP = {
    "read": ShelfType.COMPLETE,
    "to-read": ShelfType.WISHLIST,
    "currently-reading": ShelfType.PROGRESS,
    "did-not-finish": ShelfType.DROPPED,
}


class StoryGraphImporter(Task):
    class Meta:
        app_label = "journal"  # workaround bug in TypedModel

    TaskQueue = "import"
    DefaultMetadata = {
        "total": 0,
        "processed": 0,
        "skipped": 0,
        "imported": 0,
        "failed": 0,
        "visibility": 0,
        "failed_items": [],
        "file": None,
    }

    @classmethod
    def validate_file(cls, uploaded_file) -> bool:
        try:
            first_line = uploaded_file.read(200).decode("utf-8", errors="ignore")
            uploaded_file.seek(0)
            return first_line.startswith("Title,Authors,Contributors,ISBN/UID,")
        except Exception:
            return False

    @classmethod
    def find_item(cls, isbn_uid: str, title: str = "", authors: str = ""):
        # Step 1: ISBN/UID lookup in local DB (no network)
        if isbn_uid:
            id_type, id_value = detect_isbn_asin(isbn_uid.strip())
            if id_type and id_value:
                er = ExternalResource.objects.filter(
                    id_type=id_type, id_value=id_value
                ).first()
                if er and er.item:
                    return er.item

        # Step 2: Google Books search by title + author (network fallback)
        if title:
            item = cls._find_via_google_books(title, authors)
            if item:
                return item

        return None

    @classmethod
    def _find_via_google_books(cls, title: str, authors: str):
        # Build query: intitle + inauthor (first author only for precision)
        q = f"intitle:{quote_plus(title)}"
        first_author = authors.split(",")[0].strip() if authors else ""
        if first_author:
            q += f"+inauthor:{quote_plus(first_author)}"
        api_url = (
            f"https://www.googleapis.com/books/v1/volumes?country=us&q={q}&maxResults=3"
        )
        try:
            j = BasicDownloader(api_url).download().json()
            for book in j.get("items", []):
                vi = book.get("volumeInfo", {})
                result_title = vi.get("title", "")
                # Accept if either title contains the other (case-insensitive)
                if (
                    title.lower() not in result_title.lower()
                    and result_title.lower() not in title.lower()
                ):
                    continue
                url = "https://books.google.com/books?id=" + book["id"]
                site = SiteManager.get_site_by_url(url, detect_redirection=False)
                if site:
                    resource = site.get_resource_ready()
                    if resource and resource.item:
                        return resource.item
        except Exception as e:
            logger.warning(f"Google Books search failed for '{title}': {e}")
        return None

    def progress(self, mark_state: int, title: str | None = None) -> None:
        self.metadata["processed"] += 1
        match mark_state:
            case 1:
                self.metadata["imported"] += 1
            case 0:
                self.metadata["skipped"] += 1
            case _:
                self.metadata["failed"] += 1
                if title:
                    self.metadata["failed_items"].append(title)
        self.message = f"{self.metadata['imported']} imported, {self.metadata['skipped']} skipped, {self.metadata['failed']} failed"
        self.save(update_fields=["metadata", "message"])

    def run(self) -> None:
        filename = self.metadata["file"]
        visibility = self.metadata["visibility"]
        with open(filename, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                shelf_type = SHELF_MAP.get(row.get("Read Status", ""))
                if shelf_type is None:
                    self.progress(0)
                    continue

                item = self.find_item(
                    row.get("ISBN/UID", ""),
                    title=row.get("Title", ""),
                    authors=row.get("Authors", ""),
                )
                if not item:
                    logger.warning(
                        f"Could not find item for StoryGraph book: {row.get('Title')}"
                    )
                    self.progress(-1, row.get("Title"))
                    continue

                # Rating: StoryGraph uses 0.5–5.0 half-stars; NeoDB uses 1–10 integers
                rating_raw = row.get("Star Rating", "").strip()
                rating: int | None = None
                if rating_raw:
                    try:
                        rating = round(float(rating_raw) * 2) or None
                    except ValueError:
                        pass

                # Review text (may contain HTML)
                review_html = row.get("Review", "").strip()
                comment: str | None = None
                long_review: str | None = None
                if review_html:
                    has_html = "<" in review_html
                    review_text = md(review_html) if has_html else review_html
                    if not has_html and len(review_text) < 360:
                        comment = review_text
                    else:
                        long_review = review_text

                # Date: last read date for completed books, date added otherwise
                if shelf_type == ShelfType.COMPLETE and row.get("Last Date Read"):
                    date_str = row["Last Date Read"]
                else:
                    date_str = row.get("Date Added", "")

                dt = None
                if date_str:
                    try:
                        dt = make_aware(
                            datetime.strptime(date_str, "%Y/%m/%d").replace(hour=22)
                        )
                    except ValueError:
                        pass

                mark = Mark(self.user.identity, item)
                is_downgrade = (
                    mark.shelf_type == ShelfType.COMPLETE
                    and shelf_type != ShelfType.COMPLETE
                ) or (
                    mark.shelf_type in [ShelfType.PROGRESS, ShelfType.DROPPED]
                    and shelf_type == ShelfType.WISHLIST
                )
                if is_downgrade:
                    self.progress(0)
                    continue
                if mark.shelf_type == shelf_type:
                    existing_review = Review.objects.filter(
                        owner=self.user.identity, item=item
                    ).first()
                    review_body = existing_review.body if existing_review else None
                    if comment == mark.comment_text and long_review == review_body:
                        self.progress(0)
                        continue

                mark.update(
                    shelf_type,
                    comment,
                    rating,
                    visibility=visibility,
                    created_time=dt or timezone.now(),
                )
                if long_review:
                    item_title = item.title or row.get("Title", "")
                    title = _("a review of {item_title}").format(item_title=item_title)
                    Review.update_item_review(
                        item,
                        self.user.identity,
                        title,
                        long_review,
                        visibility,
                        dt or timezone.now(),
                    )
                self.progress(1)

        self.metadata["total"] = self.metadata["processed"]
        self.message = f"{self.metadata['imported']} imported, {self.metadata['skipped']} skipped, {self.metadata['failed']} failed"
        self.save(update_fields=["metadata", "message"])
