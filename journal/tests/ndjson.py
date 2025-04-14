import json
import os
import zipfile
from tempfile import TemporaryDirectory

from django.test import TestCase
from django.utils.dateparse import parse_datetime
from loguru import logger

from catalog.models import (
    Edition,
    IdType,
    Movie,
    Podcast,
    PodcastEpisode,
    TVEpisode,
    TVSeason,
    TVShow,
)
from journal.exporters import NdjsonExporter
from journal.importers import NdjsonImporter
from users.models import User

from ..models import *


class NdjsonExportImportTest(TestCase):
    databases = "__all__"
    maxDiff = None

    def setUp(self):
        self.user1 = User.register(
            email="ndjson_export@test.com", username="ndjson_exporter"
        )
        self.user2 = User.register(
            email="ndjson_import@test.com", username="ndjson_importer"
        )
        self.tag1 = Tag.objects.create(
            owner=self.user1.identity, title="favorite", pinned=True, visibility=2
        )
        self.dt = parse_datetime("2021-01-01T00:00:00Z")
        self.dt2 = parse_datetime("2021-02-01T00:00:00Z")
        self.dt3 = parse_datetime("2021-03-01T00:00:00Z")
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
        # Create podcast test items
        self.podcast = Podcast.objects.create(
            localized_title=[{"lang": "en", "text": "Test Podcast"}],
            primary_lookup_id_type=IdType.RSS,
            primary_lookup_id_value="https://example.com/feed.xml",
            host=["Test Host"],
        )
        self.podcastepisode = PodcastEpisode.objects.create(
            localized_title=[{"lang": "en", "text": "Test Episode 1"}],
            program=self.podcast,
            guid="111",
            pub_date=self.dt,
        )

    def test_ndjson_export_import(self):
        # set name and summary for user1
        identity1 = self.user1.identity
        takahe_identity1 = identity1.takahe_identity
        takahe_identity1.name = "Test User"
        takahe_identity1.summary = "Test summary"
        takahe_identity1.save()

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
            ShelfType.WISHLIST,
            "Read it?",
            None,
            ["sci-fi", "desert"],
            1,
            created_time=self.dt,
        )
        mark_book2.update(
            ShelfType.PROGRESS,
            "Reading!",
            None,
            ["sci-fi", "desert"],
            0,
            created_time=self.dt2,
        )
        mark_book2.update(
            ShelfType.COMPLETE,
            "Read.",
            None,
            ["sci-fi", "desert"],
            0,
            created_time=self.dt3,
        )

        # Movie marks with ratings
        mark_movie1 = Mark(self.user1.identity, self.movie1)
        mark_movie1.update(
            ShelfType.COMPLETE,
            "Mind-bending",
            8,
            ["mindbender", "scifi"],
            1,
            created_time=self.dt,
        )

        mark_movie2 = Mark(self.user1.identity, self.movie2)
        mark_movie2.update(
            ShelfType.WISHLIST, "Need to rewatch", None, [], 1, created_time=self.dt2
        )

        # TV show mark
        mark_tvshow = Mark(self.user1.identity, self.tvshow)
        mark_tvshow.update(
            ShelfType.WISHLIST,
            "Heard it's good",
            None,
            ["drama"],
            1,
            created_time=self.dt,
        )

        # TV episode marks
        mark_episode1 = Mark(self.user1.identity, self.tvepisode1)
        mark_episode1.update(
            ShelfType.COMPLETE,
            "Great start",
            9,
            ["pilot", "drama"],
            1,
            created_time=self.dt2,
        )

        mark_episode2 = Mark(self.user1.identity, self.tvepisode2)
        mark_episode2.update(
            ShelfType.COMPLETE, "It gets better", 9, [], 1, created_time=self.dt3
        )

        # Podcast episode mark
        mark_podcast = Mark(self.user1.identity, self.podcastepisode)
        mark_podcast.update(
            ShelfType.COMPLETE,
            "Insightful episode",
            8,
            ["tech", "interview"],
            1,
            created_time=self.dt,
        )

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
            progress_value="125",
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

        # Create TV episode note
        Note.objects.create(
            item=self.tvepisode1,
            owner=self.user1.identity,
            title="Episode thoughts",
            content="Great pilot episode. Sets up the character arcs really well.",
            visibility=1,
        )

        # Create podcast episode note
        Note.objects.create(
            item=self.podcastepisode,
            owner=self.user1.identity,
            title="Podcast episode notes",
            content="Interesting discussion about tech trends. Timestamp 23:45 has a good point about AI.",
            progress_type=Note.ProgressType.TIMESTAMP,
            progress_value="23:45",
            visibility=1,
        )

        # Create collections
        items = [self.book1, self.movie1]
        collection = Collection.objects.create(
            owner=self.user1.identity,
            title="Favorites",
            brief="My all-time favorites",
            visibility=1,
        )
        for i in items:
            collection.append_item(i)

        # Create another collection with different items
        items2 = [self.book2, self.movie2, self.tvshow]
        collection2 = Collection.objects.create(
            owner=self.user1.identity,
            title="To Review",
            brief="Items I need to review soon",
            visibility=1,
        )
        for i in items2:
            collection2.append_item(i)

        # Create shelf log entries
        logs = ShelfLogEntry.objects.filter(owner=self.user1.identity).order_by(
            "timestamp", "item_id"
        )

        # Export data to NDJSON
        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        export_path = exporter.metadata["file"]
        logger.debug(f"exported to {export_path}")
        self.assertTrue(os.path.exists(export_path))
        self.assertEqual(exporter.metadata["total"], 61)

        # Validate the NDJSON export file structure
        with TemporaryDirectory() as extract_dir:
            with zipfile.ZipFile(export_path, "r") as zip_ref:
                zip_ref.extractall(extract_dir)
                logger.debug(f"unzipped to {extract_dir}")

                # Check journal.ndjson exists
                journal_path = os.path.join(extract_dir, "journal.ndjson")
                self.assertTrue(
                    os.path.exists(journal_path), "journal.ndjson file missing"
                )

                # Check catalog.ndjson exists
                catalog_path = os.path.join(extract_dir, "catalog.ndjson")
                self.assertTrue(
                    os.path.exists(catalog_path), "catalog.ndjson file missing"
                )

                # Check attachments directory exists
                attachments_path = os.path.join(extract_dir, "attachments")
                self.assertTrue(
                    os.path.exists(attachments_path), "attachments directory missing"
                )

                # Count the number of JSON objects in journal.ndjson
                with open(journal_path, "r") as f:
                    lines = f.readlines()
                    # First line is header, rest are data
                    self.assertGreater(
                        len(lines), 1, "journal.ndjson has no data lines"
                    )

                    # Check the first line is a header
                    header = json.loads(lines[0])
                    self.assertIn("server", header, "Missing server in header")
                    self.assertIn("username", header, "Missing username in header")
                    self.assertEqual(
                        header["username"],
                        "ndjson_exporter",
                        "Wrong username in header",
                    )

                    # Count data objects by type
                    type_counts = {
                        "ShelfMember": 0,
                        "Review": 0,
                        "Note": 0,
                        "Collection": 0,
                        "ShelfLog": 0,
                        "post": 0,
                    }

                    for line in lines[1:]:
                        data = json.loads(line)
                        if "type" in data:
                            type_counts[data["type"]] = (
                                type_counts.get(data["type"], 0) + 1
                            )

                    # Verify counts
                    self.assertEqual(
                        type_counts["ShelfMember"], 8, "Expected 8 ShelfMember entries"
                    )
                    self.assertEqual(
                        type_counts["Review"], 2, "Expected 2 Review entries"
                    )
                    self.assertEqual(type_counts["Note"], 4, "Expected 4 Note entries")
                    self.assertEqual(
                        type_counts["Collection"], 2, "Expected 2 Collection entries"
                    )
                    self.assertEqual(type_counts["ShelfLog"], logs.count())

        # Now import the export file into a different user account
        importer = NdjsonImporter.create(
            user=self.user2, file=export_path, visibility=2
        )
        importer.run()
        self.assertIn("61 items imported, 0 skipped, 0 failed.", importer.message)

        # Verify imported data
        identity2 = self.user2.identity
        takahe_identity2 = identity2.takahe_identity

        # Check that name and summary were updated
        self.assertEqual(takahe_identity2.name, "Test User")
        self.assertEqual(takahe_identity2.summary, "Test summary")
        # Check marks
        mark_book1_imported = Mark(self.user2.identity, self.book1)
        self.assertEqual(mark_book1_imported.shelf_type, ShelfType.COMPLETE)
        self.assertEqual(mark_book1_imported.comment_text, "Great sci-fi classic")
        self.assertEqual(mark_book1_imported.rating_grade, 10)
        self.assertEqual(mark_book1_imported.visibility, 1)
        self.assertEqual(
            set(mark_book1_imported.tags), set(["sci-fi", "favorite", "space"])
        )

        mark_book2_imported = Mark(self.user2.identity, self.book2)
        self.assertEqual(mark_book2_imported.shelf_type, ShelfType.COMPLETE)
        self.assertEqual(mark_book2_imported.comment_text, "Read.")
        self.assertIsNone(mark_book2_imported.rating_grade)
        self.assertEqual(set(mark_book2_imported.tags), set(["sci-fi", "desert"]))
        self.assertEqual(mark_book2_imported.visibility, 0)

        mark_movie1_imported = Mark(self.user2.identity, self.movie1)
        self.assertEqual(mark_movie1_imported.shelf_type, ShelfType.COMPLETE)
        self.assertEqual(mark_movie1_imported.comment_text, "Mind-bending")
        self.assertEqual(mark_movie1_imported.rating_grade, 8)
        self.assertEqual(set(mark_movie1_imported.tags), set(["mindbender", "scifi"]))

        mark_episode1_imported = Mark(self.user2.identity, self.tvepisode1)
        self.assertEqual(mark_episode1_imported.shelf_type, ShelfType.COMPLETE)
        self.assertEqual(mark_episode1_imported.comment_text, "Great start")
        self.assertEqual(mark_episode1_imported.rating_grade, 9)
        self.assertEqual(set(mark_episode1_imported.tags), set(["pilot", "drama"]))

        # Check podcast episode mark
        mark_podcast_imported = Mark(self.user2.identity, self.podcastepisode)
        self.assertEqual(mark_podcast_imported.shelf_type, ShelfType.COMPLETE)
        self.assertEqual(mark_podcast_imported.comment_text, "Insightful episode")
        self.assertEqual(mark_podcast_imported.rating_grade, 8)
        self.assertEqual(set(mark_podcast_imported.tags), set(["tech", "interview"]))

        # Check reviews
        book1_reviews = Review.objects.filter(
            owner=self.user2.identity, item=self.book1
        )
        self.assertEqual(book1_reviews.count(), 1)
        self.assertEqual(book1_reviews[0].title, "My thoughts on Hyperion")
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

        # Check TV episode notes
        tvepisode_notes = Note.objects.filter(
            owner=self.user2.identity, item=self.tvepisode1
        )
        self.assertEqual(tvepisode_notes.count(), 1)
        self.assertEqual(tvepisode_notes[0].title, "Episode thoughts")
        self.assertIn("Sets up the character arcs", tvepisode_notes[0].content)

        # Check podcast episode notes
        podcast_notes = Note.objects.filter(
            owner=self.user2.identity, item=self.podcastepisode
        )
        self.assertEqual(podcast_notes.count(), 1)
        self.assertEqual(podcast_notes[0].title, "Podcast episode notes")
        self.assertIn(
            "Interesting discussion about tech trends", podcast_notes[0].content
        )
        self.assertEqual(podcast_notes[0].progress_type, Note.ProgressType.TIMESTAMP)
        self.assertEqual(podcast_notes[0].progress_value, "23:45")

        # Check first collection
        collections = Collection.objects.filter(
            owner=self.user2.identity, title="Favorites"
        )
        self.assertEqual(collections.count(), 1)
        self.assertEqual(collections[0].brief, "My all-time favorites")
        self.assertEqual(collections[0].visibility, 1)
        collection_items = list(collections[0].ordered_items)
        self.assertEqual([self.book1, self.movie1], collection_items)

        # Check second collection
        collections2 = Collection.objects.filter(
            owner=self.user2.identity, title="To Review"
        )
        self.assertEqual(collections2.count(), 1)
        self.assertEqual(collections2[0].brief, "Items I need to review soon")
        self.assertEqual(collections2[0].visibility, 1)

        # Check second collection items
        collection2_items = [m.item for m in collections2[0].members.all()]
        self.assertEqual(len(collection2_items), 3)
        self.assertIn(self.book2, collection2_items)
        self.assertIn(self.movie2, collection2_items)
        self.assertIn(self.tvshow, collection2_items)

        tag1 = Tag.objects.filter(owner=self.user2.identity, title="favorite").first()
        self.assertIsNotNone(tag1)
        if tag1:
            self.assertTrue(tag1.pinned)
            self.assertEqual(tag1.visibility, 2)

        # Check shelf log entries
        logs2 = ShelfLogEntry.objects.filter(owner=self.user2.identity).order_by(
            "timestamp", "item_id"
        )
        l1 = [(log.item, log.shelf_type, log.timestamp) for log in logs]
        l2 = [(log.item, log.shelf_type, log.timestamp) for log in logs2]
        self.assertEqual(l1, l2)
