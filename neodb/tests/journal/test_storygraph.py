import csv
import io
import shutil
from unittest.mock import patch

import pytest
from django.utils import timezone

from catalog.common.downloaders import set_mock_mode, use_local_response
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


def _read_matched(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {row["Title"]: row for row in csv.DictReader(f)}


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

    @pytest.fixture
    def local_response(self):
        # use_local_response can't be combined with other fixtures (its
        # wrapper only accepts self), so mock mode is a fixture here
        set_mock_mode(True)
        yield
        set_mock_mode(False)

    def _create_matching_task(self, tmp_path):
        # matching writes "<file>-matched.csv" next to the input, so copy the
        # test csv to a scratch dir first
        src = tmp_path / "storygraph_library_export.csv"
        shutil.copyfile(self.CSV_PATH, src)
        return StoryGraphImporter.create(
            self.user, phase="matching", file=str(src), filename_hint="export.csv"
        )

    def test_validate_file(self):
        with open(self.CSV_PATH, "rb") as f:
            assert StoryGraphImporter.validate_file(f)

    def test_invalid_file(self):
        bad = io.BytesIO(b"Book Id,Title,Author\n1,Foo,Bar\n")
        assert not StoryGraphImporter.validate_file(bad)

    def test_matching_phase(self, tmp_path, local_response):
        task = self._create_matching_task(tmp_path)
        # skip the catalog index so results don't depend on search availability
        with patch.object(
            StoryGraphImporter, "_match_via_local_index", return_value=None
        ):
            task.run()

        assert task.metadata["phase"] == "preview"
        # 5 local (4 by ISBN + 1 by StoryGraph UUID), 3 external (1 Google
        # Books by ISBN + 1 OpenLibrary by ISBN + 1 Google Books title search),
        # 1 unmatched (bad ISBN, no match anywhere)
        assert task.metadata["matched_local"] == 5
        assert task.metadata["matched_external"] == 3
        assert task.metadata["unmatched"] == 1

        rows = _read_matched(task.metadata["matched_file"])
        assert len(rows) == 9

        row = rows["Brave New World"]
        assert row["link"] == self.brave_new_world.url
        assert row["match_source"] == "local"
        assert row["shelf"] == "complete"
        assert row["collect_date"] == "2022-06-15"

        row = rows["1984"]
        assert row["link"] == self.nineteen_eighty_four.url
        assert row["shelf"] == "progress"
        assert row["collect_date"] == "2022-04-02"

        assert rows["The Little Prince"]["shelf"] == "dropped"
        assert rows["Fahrenheit 451"]["shelf"] == "wishlist"

        row = rows["The Memory Police"]
        assert row["link"] == self.memory_police.url
        assert row["match_source"] == "local"

        # ISBN not in local DB, matched via Google Books ISBN query
        row = rows["The Hobbit"]
        assert row["link"] == "https://books.google.com/books?id=hobbit_test_id"
        assert row["match_source"] == "googlebooks"

        # Google Books has no result for the ISBN, matched via OpenLibrary
        row = rows["Fantastic Mr Fox"]
        assert row["link"] == "https://openlibrary.org/books/OL7353617M"
        assert row["match_source"] == "openlibrary"

        # no ISBN, matched via Google Books title + author search
        row = rows["Tales from Earthsea"]
        assert row["link"] == "https://books.google.com/books?id=earthsea_test_id"
        assert row["match_source"] == "googlebooks"

        row = rows["Unknown Book"]
        assert row["link"] == ""
        assert row["match_source"] == "none"

    def test_import_phase(self, tmp_path, local_response):
        task = self._create_matching_task(tmp_path)
        with patch.object(
            StoryGraphImporter, "_match_via_local_index", return_value=None
        ):
            task.run()
        assert task.metadata["phase"] == "preview"

        task.metadata["phase"] = "importing"
        task.metadata["visibility"] = 0
        task.save(update_fields=["metadata"])
        task.run()

        assert task.metadata["phase"] == "done"
        # 8 imported, 1 skipped (unmatched row has no link)
        assert task.metadata["imported"] == 8
        assert task.metadata["skipped"] == 1
        assert task.metadata["failed"] == 0

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

        # The Memory Police: matched by StoryGraph UUID in local DB
        mark = Mark(self.identity, self.memory_police)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 8

        # Tales from Earthsea: fetched from Google Books during import, 3.5->7
        earthsea = Edition.objects.filter(
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780152047641",
        ).first()
        assert earthsea is not None
        mark = Mark(self.identity, earthsea)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 7
        assert mark.comment_text == "A great short story collection"

        # The Hobbit: fetched from Google Books by ISBN
        hobbit = Edition.objects.filter(
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780547928227",
        ).first()
        assert hobbit is not None
        mark = Mark(self.identity, hobbit)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 9

        # Fantastic Mr Fox: fetched from OpenLibrary
        fox = Edition.objects.filter(
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780140328721",
        ).first()
        assert fox is not None
        mark = Mark(self.identity, fox)
        assert mark.shelf_type == ShelfType.WISHLIST

    def test_match_by_isbn_local(self):
        assert StoryGraphImporter._match("9780060929879", "", "") == (
            self.brave_new_world.url,
            "local",
        )

    def test_match_by_storygraph_uid_local(self):
        assert StoryGraphImporter._match(self.SG_UUID, "", "") == (
            self.memory_police.url,
            "local",
        )

    def test_match_missing(self):
        # no ISBN and no title → None without any network call
        assert StoryGraphImporter._match("", "", "") is None
        # bad ISBN, no title → None
        assert StoryGraphImporter._match("notanisbn", "", "") is None

    @use_local_response
    def test_match_via_openlibrary_search(self):
        url = StoryGraphImporter._match_via_openlibrary_search(
            "Fantastic Mr Fox", "Roald Dahl"
        )
        assert url == "https://openlibrary.org/books/OL7353617M"
