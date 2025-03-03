import csv
import os
import zipfile
from tempfile import TemporaryDirectory

from django.test import TestCase
from django.utils.dateparse import parse_datetime
from loguru import logger

from catalog.models import Edition, IdType, Movie, TVEpisode, TVSeason, TVShow
from journal.exporters import CsvExporter
from journal.importers import CsvImporter
from users.models import User

from ..models import *


class CsvExportImportTest(TestCase):
    databases = "__all__"

    def setUp(self):
        # Create test items of different types
        self.book1 = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "Hyperion"}],
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780553283686",
            author=["Dan Simmons"],
            pub_year=1989,
        )
        self.book2 = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "Dune"}],
            primary_lookup_id_type=IdType.ISBN,
            primary_lookup_id_value="9780441172719",
            author=["Frank Herbert"],
            pub_year=1965,
        )
        self.movie1 = Movie.objects.create(
            localized_title=[{"lang": "en", "text": "Inception"}],
            primary_lookup_id_type=IdType.IMDB,
            primary_lookup_id_value="tt1375666",
            director=["Christopher Nolan"],
            year=2010,
        )
        self.movie2 = Movie.objects.create(
            localized_title=[{"lang": "en", "text": "The Matrix"}],
            primary_lookup_id_type=IdType.IMDB,
            primary_lookup_id_value="tt0133093",
            director=["Lana Wachowski", "Lilly Wachowski"],
            year=1999,
        )
        self.tvshow = TVShow.objects.create(
            localized_title=[{"lang": "en", "text": "Breaking Bad"}],
            primary_lookup_id_type=IdType.IMDB,
            primary_lookup_id_value="tt0903747",
            year=2008,
        )
        self.tvseason = TVSeason.objects.create(
            localized_title=[{"lang": "en", "text": "Breaking Bad Season 1"}],
            show=self.tvshow,
            season_number=1,
        )
        self.tvepisode1 = TVEpisode.objects.create(
            localized_title=[{"lang": "en", "text": "Pilot"}],
            season=self.tvseason,
            episode_number=1,
        )
        self.tvepisode2 = TVEpisode.objects.create(
            localized_title=[{"lang": "en", "text": "Cat's in the Bag..."}],
            season=self.tvseason,
            episode_number=2,
        )

        # Create user for testing
        self.user1 = User.register(email="export@test.com", username="exporter")
        self.user2 = User.register(email="import@test.com", username="importer")
        self.dt = parse_datetime("2021-01-01T00:00:00Z")

    def test_csv_export_import(self):
        # Create marks, reviews and notes for user1

        # Book marks with ratings and tags
        mark_book1 = Mark(self.user1.identity, self.book1)
        mark_book1.update(
            ShelfType.COMPLETE,
            "Great sci-fi classic",
            10,
            ["sci-fi", "favorite", "space"],
            1,
            created_time=self.dt,
        )

        mark_book2 = Mark(self.user1.identity, self.book2)
        mark_book2.update(
            ShelfType.PROGRESS, "Reading it now", None, ["sci-fi", "desert"], 1
        )

        # Movie marks with ratings
        mark_movie1 = Mark(self.user1.identity, self.movie1)
        mark_movie1.update(
            ShelfType.COMPLETE, "Mind-bending", 8, ["mindbender", "scifi"], 1
        )

        mark_movie2 = Mark(self.user1.identity, self.movie2)
        mark_movie2.update(ShelfType.WISHLIST, "Need to rewatch", None, [], 1)

        # TV show mark
        mark_tvshow = Mark(self.user1.identity, self.tvshow)
        mark_tvshow.update(ShelfType.WISHLIST, "Heard it's good", None, ["drama"], 1)

        # TV episode marks
        mark_episode1 = Mark(self.user1.identity, self.tvepisode1)
        mark_episode1.update(ShelfType.COMPLETE, "Great start", 9, [], 1)

        mark_episode2 = Mark(self.user1.identity, self.tvepisode2)
        mark_episode2.update(ShelfType.COMPLETE, "It gets better", 9, [], 1)

        # Create reviews
        Review.update_item_review(
            self.book1,
            self.user1.identity,
            "My thoughts on Hyperion",
            "A masterpiece of science fiction that weaves multiple storylines into a captivating narrative.",
            visibility=1,
            created_time=self.dt,
        )

        Review.update_item_review(
            self.movie1,
            self.user1.identity,
            "Inception Review",
            "Christopher Nolan at his best. The movie plays with reality and dreams in a fascinating way.",
            visibility=1,
        )

        # Create notes
        Note.objects.create(
            item=self.book2,
            owner=self.user1.identity,
            title="Reading progress",
            content="Just finished the first part. The world-building is incredible.\n\n - p 125",
            progress_type=Note.ProgressType.PAGE,
            progress_value="p 125",
            visibility=1,
        )

        Note.objects.create(
            item=self.tvshow,
            owner=self.user1.identity,
            title="Before watching",
            content="Things to look out for according to friends:\n- Character development\n- Color symbolism\n\n - e 0",
            progress_type=Note.ProgressType.EPISODE,
            progress_value="2",
            visibility=1,
        )

        # Export data to CSV
        exporter = CsvExporter.create(user=self.user1)
        exporter.run()
        export_path = exporter.metadata["file"]
        logger.debug(f"exported to {export_path}")
        self.assertTrue(os.path.exists(export_path))

        # Validate the number of CSV rows in the export files
        with TemporaryDirectory() as extract_dir:
            with zipfile.ZipFile(export_path, "r") as zip_ref:
                zip_ref.extractall(extract_dir)
                logger.debug(f"unzipped to {extract_dir}")

                # Expected row counts (data rows, excluding header)
                expected_data_rows = {
                    "book_mark.csv": 2,  # 2 book marks
                    "book_review.csv": 1,  # 1 book review
                    "book_note.csv": 1,  # 1 book note
                    "movie_mark.csv": 2,  # 2 movie marks
                    "movie_review.csv": 1,  # 1 movie review
                    "movie_note.csv": 0,  # No movie notes
                    "tv_mark.csv": 3,  # 3 TV marks (show + 2 episodes)
                    "tv_note.csv": 1,  # 1 TV note
                    "tv_review.csv": 0,  # No TV reviews
                    "music_mark.csv": 0,  # No music marks
                    "music_review.csv": 0,  # No music reviews
                    "music_note.csv": 0,  # No music notes
                    "game_mark.csv": 0,  # No game marks
                    "game_review.csv": 0,  # No game reviews
                    "game_note.csv": 0,  # No game notes
                    "podcast_mark.csv": 0,  # No podcast marks
                    "podcast_review.csv": 0,  # No podcast reviews
                    "podcast_note.csv": 0,  # No podcast notes
                    "performance_mark.csv": 0,  # No performance marks
                    "performance_review.csv": 0,  # No performance reviews
                    "performance_note.csv": 0,  # No performance notes
                }

                # Check each file
                for filename, expected_data_count in expected_data_rows.items():
                    file_path = os.path.join(extract_dir, filename)
                    if os.path.exists(file_path):
                        with open(file_path, "r") as file:
                            csv_reader = csv.reader(file)
                            # Skip header row
                            next(csv_reader, None)
                            # Count data rows
                            row_count = sum(1 for _ in csv_reader)
                            self.assertEqual(
                                row_count,
                                expected_data_count,
                                f"File {filename} has {row_count} data rows, expected {expected_data_count}",
                            )

                            # Check header row is present by reopening the file
                            with open(file_path, "r") as header_check:
                                first_line = next(header_check, "")
                                self.assertTrue(
                                    first_line.strip(),
                                    f"File {filename} has no header row",
                                )
                    elif expected_data_count > 0:
                        self.fail(
                            f"Expected file {filename} with {expected_data_count} data rows, but file not found"
                        )

        importer = CsvImporter.create(user=self.user2, file=export_path, visibility=2)
        importer.run()
        self.assertEqual(importer.message, "Import complete")

        # Verify imported data

        # Check marks
        mark_book1_imported = Mark(self.user2.identity, self.book1)
        self.assertEqual(mark_book1_imported.shelf_type, ShelfType.COMPLETE)
        self.assertEqual(mark_book1_imported.comment_text, "Great sci-fi classic")
        self.assertEqual(mark_book1_imported.rating_grade, 10)
        self.assertEqual(mark_book1_imported.visibility, 2)
        self.assertEqual(mark_book1_imported.created_time, self.dt)
        self.assertEqual(
            set(mark_book1_imported.tags), set(["sci-fi", "favorite", "space"])
        )

        mark_book2_imported = Mark(self.user2.identity, self.book2)
        self.assertEqual(mark_book2_imported.shelf_type, ShelfType.PROGRESS)
        self.assertEqual(mark_book2_imported.comment_text, "Reading it now")
        self.assertIsNone(mark_book2_imported.rating_grade)
        self.assertEqual(set(mark_book2_imported.tags), set(["sci-fi", "desert"]))

        mark_movie1_imported = Mark(self.user2.identity, self.movie1)
        self.assertEqual(mark_movie1_imported.shelf_type, ShelfType.COMPLETE)
        self.assertEqual(mark_movie1_imported.comment_text, "Mind-bending")
        self.assertEqual(mark_movie1_imported.rating_grade, 8)
        self.assertEqual(set(mark_movie1_imported.tags), set(["mindbender", "scifi"]))

        mark_episode1_imported = Mark(self.user2.identity, self.tvepisode1)
        self.assertEqual(mark_episode1_imported.shelf_type, ShelfType.COMPLETE)
        self.assertEqual(mark_episode1_imported.comment_text, "Great start")
        self.assertEqual(mark_episode1_imported.rating_grade, 9)

        # Check reviews
        book1_reviews = Review.objects.filter(
            owner=self.user2.identity, item=self.book1
        )
        self.assertEqual(book1_reviews.count(), 1)
        self.assertEqual(book1_reviews[0].title, "My thoughts on Hyperion")
        self.assertEqual(book1_reviews[0].created_time, self.dt)
        self.assertIn("masterpiece of science fiction", book1_reviews[0].body)

        movie1_reviews = Review.objects.filter(
            owner=self.user2.identity, item=self.movie1
        )
        self.assertEqual(movie1_reviews.count(), 1)
        self.assertEqual(movie1_reviews[0].title, "Inception Review")
        self.assertIn("Christopher Nolan", movie1_reviews[0].body)

        # Check notes
        book2_notes = Note.objects.filter(owner=self.user2.identity, item=self.book2)
        self.assertEqual(book2_notes.count(), 1)
        self.assertEqual(book2_notes[0].title, "Reading progress")
        self.assertIn("world-building is incredible", book2_notes[0].content)
        self.assertEqual(book2_notes[0].progress_type, Note.ProgressType.PAGE)
        self.assertEqual(book2_notes[0].progress_value, "125")

        tvshow_notes = Note.objects.filter(owner=self.user2.identity, item=self.tvshow)
        self.assertEqual(tvshow_notes.count(), 1)
        self.assertEqual(tvshow_notes[0].title, "Before watching")
        self.assertIn("Character development", tvshow_notes[0].content)
