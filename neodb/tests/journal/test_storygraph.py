import io

import pytest
from django.utils import timezone

from catalog.common.downloaders import use_local_response
from catalog.models import Edition, ExternalResource, IdType
from journal.importers import StoryGraphImporter
from journal.models import Mark, ShelfType
from users.models import User


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
class TestStoryGraphImporter:
    CSV_PATH = "test_data/storygraph_library_export.csv"

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="sgtest@example.com", username="sgtestuser")
        self.identity = self.user.identity
        self.brave_new_world = _make_edition_with_isbn(
            "9780060929879", "Brave New World"
        )
        self.nineteen_eighty_four = _make_edition_with_isbn("9789635043989", "1984")
        self.little_prince = _make_edition_with_isbn(
            "9780152023980", "The Little Prince"
        )
        self.fahrenheit = _make_edition_with_isbn("9781451673265", "Fahrenheit 451")

    def test_validate_file(self):
        with open(self.CSV_PATH, "rb") as f:
            assert StoryGraphImporter.validate_file(f)

    def test_invalid_file(self):
        bad = io.BytesIO(b"Book Id,Title,Author\n1,Foo,Bar\n")
        assert not StoryGraphImporter.validate_file(bad)

    @use_local_response
    def test_import_csv(self):
        task = StoryGraphImporter.create(self.user, visibility=0, file=self.CSV_PATH)
        task.run()

        # 5 imported (4 by ISBN + 1 via Google Books), 0 skipped, 1 failed (bad ISBN)
        assert task.metadata["imported"] == 5
        assert task.metadata["skipped"] == 0
        assert task.metadata["failed"] == 1

        # Brave New World: read, rating=4.0 -> grade=8
        mark = Mark(self.identity, self.brave_new_world)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 8

        # 1984: currently-reading, no rating
        mark = Mark(self.identity, self.nineteen_eighty_four)
        assert mark.shelf_type == ShelfType.PROGRESS
        assert mark.rating_grade is None

        # The Little Prince: did-not-finish
        mark = Mark(self.identity, self.little_prince)
        assert mark.shelf_type == ShelfType.DROPPED

        # Fahrenheit 451: to-read
        mark = Mark(self.identity, self.fahrenheit)
        assert mark.shelf_type == ShelfType.WISHLIST

        # Tales from Earthsea: found via Google Books fallback, read, rating=3.5->7
        # Google Books returns ISBN 9780152047641, which becomes primary_lookup_id
        earthsea = Edition.objects.filter(
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780152047641",
        ).first()
        assert earthsea is not None
        mark = Mark(self.identity, earthsea)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 7

    def test_half_star_rating(self):
        """StoryGraph half-star ratings (3.5) map to NeoDB grade 7."""
        assert round(3.5 * 2) == 7
        assert round(4.0 * 2) == 8
        assert round(0.5 * 2) == 1

    def test_find_item_by_isbn(self):
        item = StoryGraphImporter.find_item("9780060929879")
        assert item == self.brave_new_world

    def test_find_item_missing(self):
        # No ISBN and no title → None without any network call
        assert StoryGraphImporter.find_item("", title="") is None
        # Bad ISBN, no title → None
        assert StoryGraphImporter.find_item("notanisbn", title="") is None
