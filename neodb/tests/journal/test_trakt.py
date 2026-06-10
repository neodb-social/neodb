import pytest

from catalog.common.downloaders import use_local_response
from catalog.models import Movie
from catalog.models.tv import TVSeason
from journal.importers import TraktImporter
from journal.models import Collection, CollectionMember, Mark, ShelfType
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestTraktImporter:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="test@example.com", username="testuser")
        self.identity = self.user.identity

    def test_validate_file(self):
        zip_path = "test_data/trakt-export-test.zip"
        assert TraktImporter.validate_file(open(zip_path, "rb"))

    def test_validate_file_rejects_non_zip(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("not a zip")
        assert not TraktImporter.validate_file(open(f, "rb"))

    @use_local_response
    def test_trakt_import(self):
        zip_path = "test_data/trakt-export-test.zip"
        task = TraktImporter.create(self.user, visibility=0, file=zip_path)
        task.run()

        # Verify basic completion
        assert task.metadata["failed"] == 0, (
            f"Some imports failed: {task.metadata['failed_items']}"
        )
        assert task.metadata["imported"] > 0, "No items were imported"

        # Verify rated movie (Inception): marked complete with rating 9
        inception = Movie.objects.filter(primary_lookup_id_value="tt1375666").first()
        assert inception is not None, "Inception was not created"
        mark = Mark(self.identity, inception)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 9

        # Verify watched movie (Billy Lynn): marked complete, no rating
        billy = Movie.objects.filter(primary_lookup_id_value="tt2513074").first()
        assert billy is not None, "Billy Lynn was not created"
        mark = Mark(self.identity, billy)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade is None

        # Verify watched TV show resolves to TVSeason, not TVShow
        season = TVSeason.objects.filter(season_number=1, show__isnull=False).first()
        assert season is not None, "Doctor Who Season 1 was not created"
        mark = Mark(self.identity, season)
        assert mark.shelf_type == ShelfType.COMPLETE

        # Verify watchlist item (The Internet's Own Boy): marked wishlist
        tio = Movie.objects.filter(primary_lookup_id_value="tt3268458").first()
        assert tio is not None, "The Internet's Own Boy was not created"
        mark = Mark(self.identity, tio)
        assert mark.shelf_type == ShelfType.WISHLIST

        # Verify custom list was created with correct visibility
        collection = Collection.objects.filter(
            owner=self.identity, title="My Test List"
        ).first()
        assert collection is not None, "Collection was not created"
        assert collection.brief == "A test list description"
        assert collection.visibility == 0
        members = collection.ordered_members
        assert members.count() == 2
        first_member = members[0]
        assert isinstance(first_member, CollectionMember)
        assert first_member.note == "Great movie"

    @use_local_response
    def test_trakt_import_list_visibility(self):
        """Lists should honor the selected visibility."""
        zip_path = "test_data/trakt-export-test.zip"
        task = TraktImporter.create(self.user, visibility=1, file=zip_path)
        task.run()

        collection = Collection.objects.filter(
            owner=self.identity, title="My Test List"
        ).first()
        assert collection is not None
        assert collection.visibility == 1

    @use_local_response
    def test_trakt_import_no_duplicate_lists(self):
        """Re-importing should not duplicate custom lists."""
        zip_path = "test_data/trakt-export-test.zip"

        task1 = TraktImporter.create(self.user, visibility=0, file=zip_path)
        task1.run()
        assert (
            Collection.objects.filter(owner=self.identity, title="My Test List").count()
            == 1
        )

        # Second import should not create another list
        task2 = TraktImporter.create(self.user, visibility=0, file=zip_path)
        task2.run()
        assert (
            Collection.objects.filter(owner=self.identity, title="My Test List").count()
            == 1
        )

    @use_local_response
    def test_trakt_import_skip_downgrades(self):
        """Marks already in place should be skipped on re-import."""
        zip_path = "test_data/trakt-export-test.zip"

        # First import
        task1 = TraktImporter.create(self.user, visibility=0, file=zip_path)
        task1.run()
        first_imported = task1.metadata["imported"]
        assert first_imported > 0

        # Second import: all marks skipped, lists deduplicated
        task2 = TraktImporter.create(self.user, visibility=0, file=zip_path)
        task2.run()
        assert task2.metadata["failed"] == 0
        assert task2.metadata["imported"] == 0
        assert task2.metadata["skipped"] == 4
