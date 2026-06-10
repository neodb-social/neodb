import pytest
from django.utils import timezone

from catalog.common.downloaders import use_local_response
from catalog.models import Edition, ExternalResource, IdType
from journal.importers import GoodreadsImporter
from journal.models import Mark, Review, ShelfType
from users.models import User


def _make_edition_with_goodreads_id(book_id: str, title: str) -> Edition:
    edition = Edition.objects.create(title=title)
    ExternalResource.objects.create(
        item=edition,
        id_type=IdType.Goodreads,
        id_value=book_id,
        url=f"https://www.goodreads.com/book/show/{book_id}",
        scraped_time=timezone.now(),
        metadata={"title": title},
    )
    return edition


def _make_edition_with_isbn(isbn13: str, title: str) -> Edition:
    edition = Edition.objects.create(title=title)
    ExternalResource.objects.create(
        item=edition,
        id_type=IdType.ISBN,
        id_value=isbn13,
        url=f"https://openlibrary.org/isbn/{isbn13}",
        scraped_time=timezone.now(),
        metadata={"title": title},
    )
    return edition


@pytest.mark.django_db(databases="__all__")
class TestGoodreadsImporter:
    CSV_PATH = "test_data/goodreads_library_export.csv"

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="grtest@example.com", username="grtestuser")
        self.identity = self.user.identity

        # Books 1001–1006: matched by Goodreads ID
        self.book_1001 = _make_edition_with_goodreads_id("1001", "Tales from Earthsea")
        self.book_1002 = _make_edition_with_goodreads_id("1002", "Brave New World")
        self.book_1003 = _make_edition_with_goodreads_id(
            "1003", "The Left Hand of Darkness"
        )
        self.book_1004 = _make_edition_with_goodreads_id("1004", "Foundation")
        self.book_1005 = _make_edition_with_goodreads_id("1005", "Dune")
        self.book_1006 = _make_edition_with_goodreads_id("1006", "The Dispossessed")

        # Book 1008: matched by ISBN13 (no Goodreads ID in DB)
        self.book_1008 = _make_edition_with_isbn("9780152023980", "Fahrenheit 451")

    def test_validate_file(self):
        with open(self.CSV_PATH, "rb") as f:
            assert GoodreadsImporter.validate_file(f)

    def test_invalid_file(self):
        import io

        bad = io.BytesIO(b"not,a,goodreads,export\n1,2,3,4\n")
        assert not GoodreadsImporter.validate_file(bad)

    @use_local_response
    def test_import_csv(self):
        task = GoodreadsImporter.create(self.user, visibility=0, file=self.CSV_PATH)
        task.run()

        # 7 imported (1001–1006 via Goodreads ID + 1008 via ISBN13)
        # 1 skipped (1007 unknown shelf)
        # 1 failed (9999 not in DB)
        assert task.metadata["imported"] == 7
        assert task.metadata["skipped"] == 1
        assert task.metadata["failed"] == 1

        # 1001: read, rating=4 -> grade=8, HTML review -> Review object (not comment)
        mark_1001 = Mark(self.identity, self.book_1001)
        assert mark_1001.shelf_type == ShelfType.COMPLETE
        assert mark_1001.rating_grade == 8
        assert mark_1001.comment_text is None
        review_1001 = Review.objects.filter(
            owner=self.identity, item=self.book_1001
        ).first()
        assert review_1001 is not None
        assert "<b>" not in review_1001.body
        assert "<br" not in review_1001.body
        assert "bold" in review_1001.body
        assert "plain text" in review_1001.body

        # 1002: read, rating=5 -> grade=10, no review
        mark_1002 = Mark(self.identity, self.book_1002)
        assert mark_1002.shelf_type == ShelfType.COMPLETE
        assert mark_1002.rating_grade == 10
        assert not mark_1002.comment_text

        # 1003: read, rating=0 -> no grade
        mark_1003 = Mark(self.identity, self.book_1003)
        assert mark_1003.shelf_type == ShelfType.COMPLETE
        assert mark_1003.rating_grade is None

        # 1004: to-read
        mark_1004 = Mark(self.identity, self.book_1004)
        assert mark_1004.shelf_type == ShelfType.WISHLIST

        # 1005: currently-reading
        mark_1005 = Mark(self.identity, self.book_1005)
        assert mark_1005.shelf_type == ShelfType.PROGRESS

        # 1006: did-not-finish
        mark_1006 = Mark(self.identity, self.book_1006)
        assert mark_1006.shelf_type == ShelfType.DROPPED

        # 1008: matched by ISBN13, read, rating=3 -> grade=6
        mark_1008 = Mark(self.identity, self.book_1008)
        assert mark_1008.shelf_type == ShelfType.COMPLETE
        assert mark_1008.rating_grade == 6

        # 9999: not in DB, no mark created
        assert not Mark(self.identity, Edition.objects.create(title="ghost")).shelf_type

    def test_strip_isbn(self):
        assert GoodreadsImporter._strip_isbn('="9780152023980"') == "9780152023980"
        assert GoodreadsImporter._strip_isbn('=""') == ""
        assert GoodreadsImporter._strip_isbn("") == ""
        assert GoodreadsImporter._strip_isbn("9780152023980") == "9780152023980"
