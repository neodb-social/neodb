import csv
import io
import os
import zipfile
from tempfile import TemporaryDirectory

import pytest
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from catalog.models import IdType, Movie
from journal.importers import CsvImporter, DoubakImporter
from journal.models import Mark, Note, Review, ShelfType
from users.models import User

# The importer reads columns by name. "title" appears twice in the review and
# note headings and csv.DictReader resolves duplicates last-wins, so the review
# title is the later column. Pinned here so a change on either side fails.
MARK_HEADING = [
    "title",
    "info",
    "links",
    "timestamp",
    "status",
    "rating",
    "comment",
    "tags",
]
REVIEW_HEADING = ["title", "info", "links", "timestamp", "title", "content"]
NOTE_HEADING = ["title", "info", "links", "timestamp", "progress", "title", "content"]

OLD = "2021-01-01T00:00:00Z"
NEW = "2023-01-01T00:00:00Z"


def write_archive(
    directory: str, members: dict[str, tuple[list[str], list[list[str]]]]
) -> str:
    """Write a Doubak-shaped zip, one CSV per (heading, rows) pair."""
    path = os.path.join(directory, "doubak.zip")
    with zipfile.ZipFile(path, "w") as zipref:
        for name, (heading, rows) in members.items():
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(heading)
            for row in rows:
                writer.writerow(row)
            zipref.writestr(name, buf.getvalue())
    return path


@pytest.mark.django_db(databases="__all__")
class TestDoubakImportMode:
    """MERGE keeps whichever record is newer; OVERWRITE applies every row."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.movie = Movie.objects.create(
            localized_title=[{"lang": "en", "text": "Inception"}],
            primary_lookup_id_type=IdType.IMDB,
            primary_lookup_id_value="tt1375666",
            director=["Christopher Nolan"],
            release_date="2010",
        )
        self.user = User.register(email="doubak@test.com", username="doubaktester")
        self.old = parse_datetime(OLD)
        self.new = parse_datetime(NEW)

    def mark_archive(self, directory: str, timestamp: str) -> str:
        return write_archive(
            directory,
            {
                "movie_mark.csv": (
                    MARK_HEADING,
                    [
                        [
                            "Inception",
                            "imdb:tt1375666",
                            self.movie.url,
                            timestamp,
                            "complete",
                            "5",
                            "from the archive",
                            "archived",
                        ]
                    ],
                )
            },
        )

    def review_archive(self, directory: str, timestamp: str) -> str:
        return write_archive(
            directory,
            {
                "movie_review.csv": (
                    REVIEW_HEADING,
                    [
                        [
                            "Inception",
                            "imdb:tt1375666",
                            self.movie.url,
                            timestamp,
                            "On Inception",
                            "the version from the archive",
                        ]
                    ],
                )
            },
        )

    def existing_mark(self, created_time):
        Mark(self.user.identity, self.movie).update(
            ShelfType.COMPLETE,
            "written on neodb",
            10,
            ["kept"],
            1,
            created_time=created_time,
        )

    def existing_review(self, created_time):
        Review.update_item_review(
            self.movie,
            self.user.identity,
            "On Inception",
            "the version written on neodb",
            created_time=created_time,
            visibility=1,
        )

    def test_mode_defaults_to_merge(self):
        with TemporaryDirectory() as tmp:
            task = DoubakImporter.create(
                user=self.user, file=self.mark_archive(tmp, OLD)
            )
        assert task.metadata["mode"] == DoubakImporter.MERGE
        assert not task.overwrite

    def test_merge_keeps_the_newer_existing_mark(self):
        self.existing_mark(self.new)
        with TemporaryDirectory() as tmp:
            task = DoubakImporter.create(
                user=self.user,
                file=self.mark_archive(tmp, OLD),
                mode=DoubakImporter.MERGE,
            )
            task.run()
        assert task.message == "0 items imported, 1 skipped, 0 failed."
        mark = Mark(self.user.identity, self.movie)
        assert mark.comment_text == "written on neodb"
        assert mark.rating_grade == 10
        assert mark.created_time == self.new

    def test_overwrite_replaces_the_newer_existing_mark(self):
        self.existing_mark(self.new)
        with TemporaryDirectory() as tmp:
            task = DoubakImporter.create(
                user=self.user,
                file=self.mark_archive(tmp, OLD),
                mode=DoubakImporter.OVERWRITE,
            )
            task.run()
        assert task.message == "1 items imported, 0 skipped, 0 failed."
        mark = Mark(self.user.identity, self.movie)
        assert mark.comment_text == "from the archive"
        assert mark.rating_grade == 5
        assert mark.created_time == self.old

    def test_merge_still_applies_a_newer_row(self):
        # merge means "keep whichever is newer", not "never update"
        self.existing_mark(self.old)
        with TemporaryDirectory() as tmp:
            task = DoubakImporter.create(
                user=self.user,
                file=self.mark_archive(tmp, NEW),
                mode=DoubakImporter.MERGE,
            )
            task.run()
        assert task.message == "1 items imported, 0 skipped, 0 failed."
        assert Mark(self.user.identity, self.movie).comment_text == "from the archive"

    def test_merge_keeps_the_newer_existing_review(self):
        self.existing_review(self.new)
        with TemporaryDirectory() as tmp:
            task = DoubakImporter.create(
                user=self.user,
                file=self.review_archive(tmp, OLD),
                mode=DoubakImporter.MERGE,
            )
            task.run()
        assert task.message == "0 items imported, 1 skipped, 0 failed."
        review = Review.objects.get(owner=self.user.identity, item=self.movie)
        assert "written on neodb" in review.body

    def test_overwrite_replaces_the_newer_existing_review(self):
        self.existing_review(self.new)
        with TemporaryDirectory() as tmp:
            task = DoubakImporter.create(
                user=self.user,
                file=self.review_archive(tmp, OLD),
                mode=DoubakImporter.OVERWRITE,
            )
            task.run()
        assert task.message == "1 items imported, 0 skipped, 0 failed."
        review = Review.objects.get(owner=self.user.identity, item=self.movie)
        assert "from the archive" in review.body

    def test_overwrite_does_not_duplicate_an_identical_note(self):
        # notes are appended rather than replaced, so importing an identical one
        # again in overwrite mode would add a second copy, not overwrite anything
        Note.objects.create(
            item=self.movie,
            owner=self.user.identity,
            title="A note",
            content="same words",
            progress_type=None,
            progress_value=None,
            visibility=1,
        )
        with TemporaryDirectory() as tmp:
            path = write_archive(
                tmp,
                {
                    "movie_note.csv": (
                        NOTE_HEADING,
                        [
                            [
                                "Inception",
                                "imdb:tt1375666",
                                self.movie.url,
                                OLD,
                                "",
                                "A note",
                                "same words",
                            ]
                        ],
                    )
                },
            )
            task = DoubakImporter.create(
                user=self.user, file=path, mode=DoubakImporter.OVERWRITE
            )
            task.run()
        assert task.message == "0 items imported, 1 skipped, 0 failed."
        assert (
            Note.objects.filter(owner=self.user.identity, item=self.movie).count() == 1
        )

    def test_csv_importer_is_unaffected_by_the_new_hooks(self):
        # the merge rule was extracted from CsvImporter, not changed: an archive
        # older than the existing mark must still be skipped there
        self.existing_mark(self.new)
        with TemporaryDirectory() as tmp:
            task = CsvImporter.create(user=self.user, file=self.mark_archive(tmp, OLD))
            task.run()
        assert task.message == "0 items imported, 1 skipped, 0 failed."
        assert "mode" not in CsvImporter.DefaultMetadata
        assert Mark(self.user.identity, self.movie).comment_text == "written on neodb"


@pytest.mark.django_db(databases="__all__")
class TestDoubakValidateFile:
    def test_accepts_an_archive_holding_a_recognised_csv(self):
        with TemporaryDirectory() as tmp:
            path = write_archive(tmp, {"movie_mark.csv": (MARK_HEADING, [])})
            with open(path, "rb") as f:
                assert DoubakImporter.validate_file(f)

    def test_rejects_a_zip_without_one(self):
        with TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "other.zip")
            with zipfile.ZipFile(path, "w") as zipref:
                zipref.writestr("journal.ndjson", "{}")
            with open(path, "rb") as f:
                assert not DoubakImporter.validate_file(f)

    def test_rejects_something_that_is_not_a_zip(self):
        with TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "not.zip")
            with open(path, "w") as f:
                f.write("title,info,links\n")
            with open(path, "rb") as f:
                assert not DoubakImporter.validate_file(f)

    def test_rejects_a_missing_file(self):
        assert not DoubakImporter.validate_file(None)


@pytest.mark.django_db(databases="__all__")
class TestDoubakProgress:
    """The progress UI is inherited, so what is asserted here is the contract it
    needs rather than any code of this importer's own.

    ``user_task_status.html`` draws its bar only when ``metadata.total`` and
    ``metadata.processed`` are *both* truthy, and reaches the poller through
    ``task.type``. None of those three are written by DoubakImporter — they come
    from ``CsvImporter.run`` and ``BaseImporter.progress``, which it does not
    override. That is exactly why they are worth pinning: an override added
    later would silently leave the bar at zero, and the import would still
    finish and still report the right totals at the end.
    """

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.movie = Movie.objects.create(
            localized_title=[{"lang": "en", "text": "Inception"}],
            primary_lookup_id_type=IdType.IMDB,
            primary_lookup_id_value="tt1375666",
            director=["Christopher Nolan"],
            release_date="2010",
        )
        self.user = User.register(
            email="doubakprogress@test.com", username="doubakprogress"
        )

    def two_row_archive(self, directory: str) -> str:
        """One mark and one review, so `total` has to sum across two files."""
        return write_archive(
            directory,
            {
                "movie_mark.csv": (
                    MARK_HEADING,
                    [
                        [
                            "Inception",
                            "imdb:tt1375666",
                            self.movie.url,
                            OLD,
                            "complete",
                            "5",
                            "from the archive",
                            "archived",
                        ]
                    ],
                ),
                "movie_review.csv": (
                    REVIEW_HEADING,
                    [
                        [
                            "Inception",
                            "imdb:tt1375666",
                            self.movie.url,
                            OLD,
                            "On Inception",
                            "the version from the archive",
                        ]
                    ],
                ),
            },
        )

    def run_import(self, directory: str) -> DoubakImporter:
        task = DoubakImporter.create(
            user=self.user, file=self.two_row_archive(directory)
        )
        task.run()
        return task

    def test_counts_every_row_across_files(self):
        with TemporaryDirectory() as tmp:
            task = self.run_import(tmp)
        assert task.metadata["total"] == 2
        assert task.metadata["processed"] == 2

    def test_progress_is_saved_as_it_goes_not_only_at_the_end(self):
        # The poller re-reads the row; counters kept only in memory would leave
        # the bar at zero for the whole run and then jump straight to done.
        with TemporaryDirectory() as tmp:
            task = self.run_import(tmp)
        stored = DoubakImporter.objects.get(pk=task.pk)
        assert stored.metadata["processed"] == 2
        assert stored.metadata["total"] == 2

    def test_type_is_the_string_the_status_view_matches_on(self):
        # users/views/data.py dispatches on this literal, and the template
        # builds the poll URL from it. A mismatch redirects instead of
        # erroring, so the bar would simply never appear.
        with TemporaryDirectory() as tmp:
            task = DoubakImporter.create(user=self.user, file=self.two_row_archive(tmp))
        assert task.type == "journal.doubakimporter"

    def test_status_endpoint_renders_the_bar(self, client):
        # run() is called directly rather than through _execute, so the task is
        # still pending — which is what the mid-run render looks like.
        with TemporaryDirectory() as tmp:
            task = self.run_import(tmp)
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(reverse("users:user_task_status", args=[task.type]))
        assert response.status_code == 200
        assert '<progress value="2" max="2">' in response.content.decode()
