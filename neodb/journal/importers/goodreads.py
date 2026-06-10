import csv
from datetime import datetime

from django.utils import timezone
from django.utils.timezone import make_aware
from django.utils.translation import gettext as _
from loguru import logger
from markdownify import markdownify as md

from catalog.common import *
from catalog.common.downloaders import *
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


class GoodreadsImporter(Task):
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
        "failed_urls": [],
        "file": None,
    }

    @classmethod
    def validate_file(cls, uploaded_file) -> bool:
        try:
            first_line = uploaded_file.read(200).decode("utf-8", errors="ignore")
            uploaded_file.seek(0)
            return first_line.startswith("Book Id,")
        except Exception:
            return False

    @staticmethod
    def _strip_isbn(s: str) -> str:
        s = s.strip()
        if s.startswith('="') and s.endswith('"'):
            return s[2:-1]
        return s

    @classmethod
    def find_item(cls, book_id: str, isbn13_raw: str, isbn_raw: str):
        # Step 1: DB lookup by Goodreads ID (no network call)
        site = SiteManager.get_site_by_id(IdType.Goodreads, book_id)
        if site:
            item = site.get_item()
            if item:
                return item

        # Step 2: DB lookup by ISBN13 (no scraping)
        isbn13 = cls._strip_isbn(isbn13_raw)
        if isbn13:
            er = ExternalResource.objects.filter(
                id_type=IdType.ISBN, id_value=isbn13
            ).first()
            if er and er.item:
                return er.item

        # Step 3: DB lookup by ISBN10 / ASIN (no scraping)
        isbn = cls._strip_isbn(isbn_raw)
        if isbn:
            id_type, id_value = detect_isbn_asin(isbn)
            if id_type and id_value:
                er = ExternalResource.objects.filter(
                    id_type=id_type, id_value=id_value
                ).first()
                if er and er.item:
                    return er.item

        # Step 4: Scrape Goodreads
        try:
            scrape_site = SiteManager.get_site_by_url(
                f"https://www.goodreads.com/book/show/{book_id}",
                detect_redirection=False,
            )
            if scrape_site:
                resource = scrape_site.get_resource_ready()
                if resource:
                    item = scrape_site.get_item()
                    if item:
                        return item
        except Exception as e:
            logger.error(f"Error scraping Goodreads book {book_id}: {e}")

        return None

    def progress(self, mark_state: int, book_id: str | None = None) -> None:
        self.metadata["processed"] += 1
        match mark_state:
            case 1:
                self.metadata["imported"] += 1
            case 0:
                self.metadata["skipped"] += 1
            case _:
                self.metadata["failed"] += 1
                if book_id:
                    self.metadata["failed_urls"].append(book_id)
        self.message = f"{self.metadata['imported']} imported, {self.metadata['skipped']} skipped, {self.metadata['failed']} failed"
        self.save(update_fields=["metadata", "message"])

    def run(self) -> None:
        filename = self.metadata["file"]
        visibility = self.metadata["visibility"]
        with open(filename, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                shelf_type = SHELF_MAP.get(row["Exclusive Shelf"])
                if shelf_type is None:
                    self.progress(0)
                    continue

                book_id = row["Book Id"]
                item = self.find_item(book_id, row["ISBN13"], row["ISBN"])
                if not item:
                    logger.warning(f"Could not find item for Goodreads book {book_id}")
                    self.progress(-1, book_id)
                    continue

                rating_raw = int(row["My Rating"] or 0)
                rating = rating_raw * 2 if rating_raw else None

                review_html = row.get("My Review", "").strip()
                comment: str | None = None
                long_review: str | None = None
                if review_html:
                    has_html = "<" in review_html
                    review_text = md(review_html) if has_html else review_html
                    if not has_html and len(review_text) < 360:
                        comment = review_text
                    else:
                        long_review = review_text

                if shelf_type == ShelfType.COMPLETE and row.get("Date Read"):
                    date_str = row["Date Read"]
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
                    item_title = item.title or row["Title"]
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
