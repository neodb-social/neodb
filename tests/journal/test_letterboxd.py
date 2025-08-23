import pytest

from catalog.common.downloaders import use_local_response
from catalog.models import Movie
from journal.importers import LetterboxdImporter
from journal.models import Mark, ShelfType
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestLetterboxdImporter:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="test@example.com", username="testuser")
        self.identity = self.user.identity

    @use_local_response
    def test_letterboxd_import_process_complete_normally(self):
        zip_path = "test_data/letterboxd-neodb-2025-08-08-02-36-utc.zip"
        assert LetterboxdImporter.validate_file(open(zip_path, "rb")), (
            "Unable to validate the provided export"
        )

        task = LetterboxdImporter.create(self.user, visibility=0, file=zip_path)
        task.run()

        # Verify the task completed successfully
        assert task.metadata["imported"] > 0, "No items were imported"
        assert task.metadata["failed"] == 0, "Some imports failed"

        movie = Movie.objects.filter(primary_lookup_id_value="tt3268458").first()
        assert movie is not None, "Movie with IMDB ID tt3268458 was not created"

        mark = Mark(self.identity, movie)
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.rating_grade == 10
        assert mark.comment_text == "this is a comment."
        assert mark.tags == ["hacktivist"]
