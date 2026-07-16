import io
from unittest.mock import patch

import pytest
from django.utils import timezone

from catalog.common.downloaders import use_local_response
from catalog.models import Edition, ExternalResource, IdType
from journal.importers import StoryGraphImporter
from journal.models import Mark, ShelfType
from users.models import User


def _make_edition_with_resource(id_type: IdType, id_value: str, title: str) -> Edition:
    edition = Edition.objects.create(title=title)
    ExternalResource.objects.create(
        item=edition,
        id_type=id_type,
        id_value=id_value,
        url=f"https://example.org/{id_type}/{id_value}",
        scraped_time=timezone.now(),
        metadata={"title": title},
    )
    return edition


def _make_edition_with_isbn(isbn13: str, title: str) -> Edition:
    return _make_edition_with_resource(IdType.ISBN, isbn13, title)


@pytest.mark.django_db(databases="__all__")
class TestStoryGraphImporter:
    CSV_PATH = "test_data/storygraph_library_export.csv"
    SG_UUID = "fbdd6b7c-f512-47f2-aa94-d8bf0d5f5175"

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
        self.memory_police = _make_edition_with_resource(
            IdType.StoryGraph, self.SG_UUID, "The Memory Police"
        )

    def test_validate_file(self):
        with open(self.CSV_PATH, "rb") as f:
            assert StoryGraphImporter.validate_file(f)

    def test_invalid_file(self):
        bad = io.BytesIO(b"Book Id,Title,Author\n1,Foo,Bar\n")
        assert not StoryGraphImporter.validate_file(bad)

    @use_local_response
    def test_import_csv(self):
        task = StoryGraphImporter.create(self.user, visibility=0, file=self.CSV_PATH)
        # skip the catalog index so results don't depend on search availability
        with patch.object(
            StoryGraphImporter, "_find_via_local_index", return_value=None
        ):
            task.run()

        # 8 imported (4 by local ISBN + 1 by StoryGraph UUID + 1 via Google Books
        # title search + 1 via Google Books ISBN + 1 via OpenLibrary ISBN),
        # 0 skipped, 1 failed (bad ISBN, no match anywhere)
        assert task.metadata["imported"] == 8
        assert task.metadata["skipped"] == 0
        assert task.metadata["failed"] == 1
        assert task.metadata["failed_items"] == ["Unknown Book"]

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

        # The Memory Police: matched by StoryGraph UUID in local DB, no network
        mark = Mark(self.identity, self.memory_police)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 8

        # Tales from Earthsea: found via Google Books title search, read, 3.5->7
        earthsea = Edition.objects.filter(
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780152047641",
        ).first()
        assert earthsea is not None
        mark = Mark(self.identity, earthsea)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 7

        # The Hobbit: ISBN not in local DB, fetched from Google Books by ISBN
        hobbit = Edition.objects.filter(
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780547928227",
        ).first()
        assert hobbit is not None
        mark = Mark(self.identity, hobbit)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 9

        # Fantastic Mr Fox: Google Books has no result for the ISBN,
        # fetched from OpenLibrary by ISBN instead
        fox = Edition.objects.filter(
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780140328721",
        ).first()
        assert fox is not None
        mark = Mark(self.identity, fox)
        assert mark.shelf_type == ShelfType.WISHLIST

    def test_find_item_by_isbn(self):
        item = StoryGraphImporter.find_item("9780060929879")
        assert item == self.brave_new_world

    def test_find_item_by_storygraph_uid(self):
        item = StoryGraphImporter.find_item(self.SG_UUID)
        assert item == self.memory_police

    @use_local_response
    def test_find_via_openlibrary_search(self):
        item = StoryGraphImporter._find_via_openlibrary_search(
            "Fantastic Mr Fox", "Roald Dahl"
        )
        assert item is not None
        assert item.primary_lookup_id_type == IdType.ISBN
        assert item.primary_lookup_id_value == "9780140328721"

    def test_find_item_missing(self):
        # No ISBN and no title → None without any network call
        assert StoryGraphImporter.find_item("", title="") is None
        # Bad ISBN, no title → None
        assert StoryGraphImporter.find_item("notanisbn", title="") is None
