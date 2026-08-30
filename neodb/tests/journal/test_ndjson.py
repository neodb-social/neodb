import json
import os
import zipfile
from io import BytesIO
from tempfile import TemporaryDirectory

import pytest
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils.dateparse import parse_datetime
from loguru import logger
from PIL import Image

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
from journal.importers import CsvImporter, NdjsonImporter
from journal.models import *
from journal.models.common import Debris
from journal.search import JournalIndex
from takahe.utils import Takahe
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestNdjsonExportImport:
    maxDiff = None

    @pytest.fixture(autouse=True)
    def setup_data(self):
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
            release_date="2010",
        )
        self.movie2 = Movie.objects.create(
            localized_title=[{"lang": "en", "text": "The Matrix"}],
            primary_lookup_id_type=IdType.IMDB,
            primary_lookup_id_value="tt0133093",
            director=["Lana Wachowski", "Lilly Wachowski"],
            release_date="1999",
        )
        self.tvshow = TVShow.objects.create(
            localized_title=[{"lang": "en", "text": "Breaking Bad"}],
            primary_lookup_id_type=IdType.IMDB,
            primary_lookup_id_value="tt0903747",
            release_date="2008",
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
        assert os.path.exists(export_path)
        assert exporter.metadata["total"] == 61

        # Validate the NDJSON export file structure
        with TemporaryDirectory() as extract_dir:
            with zipfile.ZipFile(export_path, "r") as zip_ref:
                zip_ref.extractall(extract_dir)
                logger.debug(f"unzipped to {extract_dir}")

                # Check journal.ndjson exists
                journal_path = os.path.join(extract_dir, "journal.ndjson")
                assert os.path.exists(journal_path), "journal.ndjson file missing"

                # Check catalog.ndjson exists
                catalog_path = os.path.join(extract_dir, "catalog.ndjson")
                assert os.path.exists(catalog_path), "catalog.ndjson file missing"

                # Check attachments directory exists
                attachments_path = os.path.join(extract_dir, "attachments")
                assert os.path.exists(attachments_path), "attachments directory missing"

                # Count the number of JSON objects in journal.ndjson
                with open(journal_path, "r") as f:
                    lines = f.readlines()
                    # First line is header, rest are data
                    assert len(lines) > 1, "journal.ndjson has no data lines"

                    # Check the first line is a header
                    header = json.loads(lines[0])
                    assert "server" in header, "Missing server in header"
                    assert "username" in header, "Missing username in header"
                    assert header["username"] == "ndjson_exporter", (
                        "Wrong username in header"
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
                    assert type_counts["ShelfMember"] == 8, (
                        "Expected 8 ShelfMember entries"
                    )
                    assert type_counts["Review"] == 2, "Expected 2 Review entries"
                    assert type_counts["Note"] == 4, "Expected 4 Note entries"
                    assert type_counts["Collection"] == 2, (
                        "Expected 2 Collection entries"
                    )
                    assert type_counts["ShelfLog"] == logs.count()

        # Now import the export file into a different user account
        importer = NdjsonImporter.create(
            user=self.user2, file=export_path, visibility=2
        )
        importer.run()
        assert "61 items imported, 0 skipped, 0 failed." in importer.message

        # Verify imported data
        identity2 = self.user2.identity
        takahe_identity2 = identity2.takahe_identity

        # Check that name and summary were updated
        assert takahe_identity2.name == "Test User"
        assert takahe_identity2.summary == "Test summary"
        # Check marks
        mark_book1_imported = Mark(self.user2.identity, self.book1)
        assert mark_book1_imported.shelf_type == ShelfType.COMPLETE
        assert mark_book1_imported.comment_text == "Great sci-fi classic"
        assert mark_book1_imported.rating_grade == 10
        assert mark_book1_imported.visibility == 1
        assert set(mark_book1_imported.tags) == set(["sci-fi", "favorite", "space"])

        mark_book2_imported = Mark(self.user2.identity, self.book2)
        assert mark_book2_imported.shelf_type == ShelfType.COMPLETE
        assert mark_book2_imported.comment_text == "Read."
        assert mark_book2_imported.rating_grade is None
        assert set(mark_book2_imported.tags) == set(["sci-fi", "desert"])
        assert mark_book2_imported.visibility == 0

        mark_movie1_imported = Mark(self.user2.identity, self.movie1)
        assert mark_movie1_imported.shelf_type == ShelfType.COMPLETE
        assert mark_movie1_imported.comment_text == "Mind-bending"
        assert mark_movie1_imported.rating_grade == 8
        assert set(mark_movie1_imported.tags) == set(["mindbender", "scifi"])

        mark_episode1_imported = Mark(self.user2.identity, self.tvepisode1)
        assert mark_episode1_imported.shelf_type == ShelfType.COMPLETE
        assert mark_episode1_imported.comment_text == "Great start"
        assert mark_episode1_imported.rating_grade == 9
        assert set(mark_episode1_imported.tags) == set(["pilot", "drama"])

        # Check podcast episode mark
        mark_podcast_imported = Mark(self.user2.identity, self.podcastepisode)
        assert mark_podcast_imported.shelf_type == ShelfType.COMPLETE
        assert mark_podcast_imported.comment_text == "Insightful episode"
        assert mark_podcast_imported.rating_grade == 8
        assert set(mark_podcast_imported.tags) == set(["tech", "interview"])

        # Check reviews
        book1_reviews = Review.objects.filter(
            owner=self.user2.identity, item=self.book1
        )
        assert book1_reviews.count() == 1
        assert book1_reviews[0].title == "My thoughts on Hyperion"
        assert "masterpiece of science fiction" in book1_reviews[0].body

        movie1_reviews = Review.objects.filter(
            owner=self.user2.identity, item=self.movie1
        )
        assert movie1_reviews.count() == 1
        assert movie1_reviews[0].title == "Inception Review"
        assert "Christopher Nolan" in movie1_reviews[0].body

        # Check notes
        book2_notes = Note.objects.filter(owner=self.user2.identity, item=self.book2)
        assert book2_notes.count() == 1
        assert book2_notes[0].title == "Reading progress"
        assert "world-building is incredible" in book2_notes[0].content
        assert book2_notes[0].progress_type == Note.ProgressType.PAGE
        assert book2_notes[0].progress_value == "125"

        tvshow_notes = Note.objects.filter(owner=self.user2.identity, item=self.tvshow)
        assert tvshow_notes.count() == 1
        assert tvshow_notes[0].title == "Before watching"
        assert "Character development" in tvshow_notes[0].content

        # Check TV episode notes
        tvepisode_notes = Note.objects.filter(
            owner=self.user2.identity, item=self.tvepisode1
        )
        assert tvepisode_notes.count() == 1
        assert tvepisode_notes[0].title == "Episode thoughts"
        assert "Sets up the character arcs" in tvepisode_notes[0].content

        # Check podcast episode notes
        podcast_notes = Note.objects.filter(
            owner=self.user2.identity, item=self.podcastepisode
        )
        assert podcast_notes.count() == 1
        assert podcast_notes[0].title == "Podcast episode notes"
        assert "Interesting discussion about tech trends" in podcast_notes[0].content
        assert podcast_notes[0].progress_type == Note.ProgressType.TIMESTAMP
        assert podcast_notes[0].progress_value == "23:45"

        # Check first collection
        collections = Collection.objects.filter(
            owner=self.user2.identity, title="Favorites"
        )
        assert collections.count() == 1
        assert collections[0].brief == "My all-time favorites"
        assert collections[0].visibility == 1
        collection_items = list(collections[0].ordered_items)
        assert [self.book1, self.movie1] == collection_items

        # Check second collection
        collections2 = Collection.objects.filter(
            owner=self.user2.identity, title="To Review"
        )
        assert collections2.count() == 1
        assert collections2[0].brief == "Items I need to review soon"
        assert collections2[0].visibility == 1

        # Check second collection items
        collection2_items = [m.item for m in collections2[0].members.all()]
        assert len(collection2_items) == 3
        assert self.book2 in collection2_items
        assert self.movie2 in collection2_items
        assert self.tvshow in collection2_items

        tag1 = Tag.objects.filter(owner=self.user2.identity, title="favorite").first()
        assert tag1 is not None
        if tag1:
            assert tag1.pinned
            assert tag1.visibility == 2

        # Check shelf log entries
        logs2 = ShelfLogEntry.objects.filter(owner=self.user2.identity).order_by(
            "timestamp", "item_id"
        )
        l1 = [(log.item, log.shelf_type, log.timestamp) for log in logs]
        l2 = [(log.item, log.shelf_type, log.timestamp) for log in logs2]
        assert l1 == l2

    def test_ndjson_collection_extra_fields(self):
        """collaborative and query fields round-trip through export/import."""
        Collection.objects.create(
            owner=self.user1.identity,
            title="Collab Collection",
            brief="shared",
            visibility=0,
            collaborative=1,
            query="sci-fi",
        )

        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()

        importer = NdjsonImporter.create(
            user=self.user2, file=exporter.metadata["file"], visibility=0
        )
        importer.run()

        imported = Collection.objects.filter(
            owner=self.user2.identity, title="Collab Collection"
        ).first()
        assert imported is not None
        assert imported.collaborative == 1
        assert imported.query == "sci-fi"

    def test_ndjson_reimport_dedup(self):
        """Re-importing the same file does not create duplicate collections or notes."""
        mark = Mark(self.user1.identity, self.book1)
        mark.update(ShelfType.COMPLETE, created_time=self.dt)
        Collection.objects.create(
            owner=self.user1.identity,
            title="Dedup Collection",
            brief="test",
            visibility=0,
        )
        Note.objects.create(
            item=self.book1,
            owner=self.user1.identity,
            title="Dedup Note",
            content="content",
            visibility=0,
            created_time=self.dt,
        )

        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        export_path = exporter.metadata["file"]

        NdjsonImporter.create(user=self.user2, file=export_path, visibility=0).run()
        NdjsonImporter.create(user=self.user2, file=export_path, visibility=0).run()

        assert (
            Collection.objects.filter(
                owner=self.user2.identity, title="Dedup Collection"
            ).count()
            == 1
        )
        assert (
            Note.objects.filter(
                owner=self.user2.identity, item=self.book1, title="Dedup Note"
            ).count()
            == 1
        )

    def test_ndjson_newer_import_updates_instead_of_duplicating(self):
        """Importing newer data updates existing Comment/Review rows in place;
        creating another row would duplicate (owner, item) — the same
        corruption behind Sentry EGGPLANT-1GP."""
        importer = NdjsonImporter.create(user=self.user2, file="x.zip", visibility=0)
        importer.items = {self.book1.absolute_url: self.book1}
        owner = self.user2.identity

        Comment.objects.create(
            item=self.book1,
            owner=owner,
            text="old comment",
            visibility=0,
            created_time=self.dt,
        )
        result = importer.import_comment(
            {
                "visibility": 0,
                "content": {
                    "withRegardTo": self.book1.absolute_url,
                    "content": "newer comment",
                    "published": "2021-02-01T00:00:00Z",
                },
            }
        )
        assert result == "imported"
        comment = Comment.objects.get(owner=owner, item=self.book1)
        assert comment.text == "newer comment"
        assert comment.created_time == self.dt2

        Review.objects.create(
            item=self.book1,
            owner=owner,
            title="My Review",
            body="old body",
            visibility=0,
            created_time=self.dt,
        )
        result = importer.import_review(
            {
                "visibility": 0,
                "content": {
                    "withRegardTo": self.book1.absolute_url,
                    "name": "My Review",
                    "content": "newer body",
                    "published": "2021-02-01T00:00:00Z",
                },
            }
        )
        assert result == "imported"
        review = Review.objects.get(owner=owner, item=self.book1)
        assert review.body == "newer body"
        assert review.created_time == self.dt2

    def test_ndjson_article_round_trip(self):
        """Standalone Articles round-trip through export and import."""
        Article.update_local_article(
            owner=self.user1.identity,
            title="My Long Read",
            body="**Bold** opening line.\n\nWith a second paragraph.",
            summary="An essay",
            sensitive=False,
            visibility=0,
            tags=["essays", "longform"],
        )
        Article.update_local_article(
            owner=self.user1.identity,
            title="Sensitive Take",
            body="content body",
            summary="careful",
            sensitive=True,
            visibility=1,
            tags=["opinion"],
        )

        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        export_path = exporter.metadata["file"]

        # Bundle should advertise both Article rows on the journal stream.
        with zipfile.ZipFile(export_path, "r") as zf:
            with TemporaryDirectory() as tmpdir:
                zf.extractall(tmpdir)
                journal_path = next(
                    os.path.join(root, "journal.ndjson")
                    for root, _dirs, files in os.walk(tmpdir)
                    if "journal.ndjson" in files
                )
                with open(journal_path) as f:
                    lines = f.readlines()
        type_counts: dict[str, int] = {}
        for line in lines[1:]:  # skip header
            data = json.loads(line)
            t = data.get("type")
            if t:
                type_counts[t] = type_counts.get(t, 0) + 1
        assert type_counts.get("Article") == 2

        # Import into user2 and verify both articles land with correct
        # title/body/summary/sensitive/tags/visibility.
        importer = NdjsonImporter.create(
            user=self.user2, file=export_path, visibility=0
        )
        importer.run()
        assert importer.metadata["failed"] == 0

        a1 = Article.objects.get(owner=self.user2.identity, title="My Long Read")
        assert "**Bold** opening line." in a1.body
        assert a1.summary == "An essay"
        assert a1.sensitive is False
        assert sorted(a1.normalized_tags) == ["essays", "longform"]
        assert a1.word_count == len(a1.plain_content.split())

        a2 = Article.objects.get(owner=self.user2.identity, title="Sensitive Take")
        assert a2.sensitive is True
        assert a2.summary == "careful"
        assert a2.normalized_tags == ["opinion"]

    def test_ndjson_article_cover_round_trip(self, tmp_path):
        """A featured image is bundled on export and restored on import."""
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            buf = BytesIO()
            Image.new("RGB", (2, 2), "blue").save(buf, format="PNG")
            Article.update_local_article(
                owner=self.user1.identity,
                title="Covered Read",
                body="body",
                visibility=0,
                cover=SimpleUploadedFile("cover.png", buf.getvalue(), "image/png"),
            )
            exporter = NdjsonExporter.create(user=self.user1)
            exporter.run()
            export_path = exporter.metadata["file"]

            importer = NdjsonImporter.create(
                user=self.user2, file=export_path, visibility=0
            )
            importer.run()
            assert importer.metadata["failed"] == 0

            imported = Article.objects.get(
                owner=self.user2.identity, title="Covered Read"
            )
            assert str(imported.cover) != settings.DEFAULT_ITEM_COVER
            assert (imported.cover_image_url or "").endswith(".png")

    def test_ndjson_article_reimport_dedup(self):
        """Re-importing the same Article bundle doesn't duplicate rows."""
        Article.update_local_article(
            owner=self.user1.identity,
            title="Dedup Article",
            body="body",
            visibility=0,
        )
        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        export_path = exporter.metadata["file"]
        NdjsonImporter.create(user=self.user2, file=export_path, visibility=0).run()
        NdjsonImporter.create(user=self.user2, file=export_path, visibility=0).run()
        assert (
            Article.objects.filter(
                owner=self.user2.identity, title="Dedup Article"
            ).count()
            == 1
        )

    def test_ndjson_tagmember_catalog(self):
        """Items only referenced by tags (no shelf) appear in catalog.ndjson."""
        # Tag book1 without shelving it — item must end up in catalog via TagMember ref
        TagManager.tag_item_for_owner(self.user1.identity, self.book1, ["tag-only"])

        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()

        with zipfile.ZipFile(exporter.metadata["file"], "r") as zf:
            catalog = zf.read("catalog.ndjson").decode()
        assert self.book1.absolute_url in catalog

        importer = NdjsonImporter.create(
            user=self.user2, file=exporter.metadata["file"], visibility=0
        )
        importer.run()
        assert importer.metadata["failed"] == 0
        assert TagMember.objects.filter(
            owner=self.user2.identity, parent__title="tag-only"
        ).exists()

    def test_resolve_temp_path_rejects_traversal(self):
        """Paths referenced by user-supplied NDJSON must stay inside temp_dir."""
        importer = NdjsonImporter.create(user=self.user2, file="x.zip", visibility=0)
        with TemporaryDirectory() as tmp:
            importer.temp_dir = tmp
            # Sentinel file inside temp_dir resolves fine
            inside = os.path.join(tmp, "cover.jpg")
            with open(inside, "wb") as f:
                f.write(b"x")
            assert importer._resolve_temp_path("cover.jpg") == os.path.realpath(inside)
            # Traversal attempts resolve to None
            assert importer._resolve_temp_path("../../etc/passwd") is None
            assert importer._resolve_temp_path("/etc/passwd") is None
            assert importer._resolve_temp_path("") is None
            assert importer._resolve_temp_path(None) is None

    def test_import_collection_cover_inside_temp_dir(self):
        """import_collection accepts a cover that resolves inside temp_dir
        and rejects one that escapes it."""
        importer = NdjsonImporter.create(user=self.user2, file="x.zip", visibility=0)
        with TemporaryDirectory() as tmp:
            importer.temp_dir = tmp
            cover_path = os.path.join(tmp, "cover.jpg")
            # Minimal valid JPEG (1x1 pixel) so cover.save accepts it
            with open(cover_path, "wb") as f:
                f.write(
                    bytes.fromhex(
                        "ffd8ffe000104a46494600010101006000600000ffdb004300080606070605"
                        "08070707090908"
                        + "0a"
                        + "10"
                        + "0b" * 5
                        + "0c"
                        + "13" * 4
                        + "0e" * 6
                        + "0f" * 8
                        + "ffd9"
                    )
                )
            data = {
                "content": {
                    "name": "Cover Import OK",
                    "content": "",
                    "published": "2024-01-01T00:00:00+00:00",
                },
                "visibility": 0,
                "cover": "cover.jpg",
                "items": [],
            }
            assert importer.import_collection(data) == "imported"
            coll = Collection.objects.get(
                owner=self.user2.identity, title="Cover Import OK"
            )
            # Cover populated from the on-disk file (not the model default)
            assert coll.cover.name and coll.cover.name != settings.DEFAULT_ITEM_COVER

            data["content"]["name"] = "Cover Traversal Rejected"
            data["cover"] = "../../etc/passwd"
            assert importer.import_collection(data) == "imported"
            coll = Collection.objects.get(
                owner=self.user2.identity, title="Cover Traversal Rejected"
            )
            # Traversal path is rejected: no cover stored, default still in place
            assert not coll.cover.name or coll.cover.name == settings.DEFAULT_ITEM_COVER

            # A cover entry pointing at a directory inside temp_dir resolves
            # cleanly but must not raise IsADirectoryError on open().
            os.mkdir(os.path.join(tmp, "subdir"))
            data["content"]["name"] = "Cover Directory Skipped"
            data["cover"] = "subdir"
            assert importer.import_collection(data) == "imported"
            coll = Collection.objects.get(
                owner=self.user2.identity, title="Cover Directory Skipped"
            )
            assert not coll.cover.name or coll.cover.name == settings.DEFAULT_ITEM_COVER

    def test_ndjson_export_skips_debris(self):
        """A Debris tombstone must not abort the export.

        Debris is a Content subclass with no ap_object, so walking
        Content.__subclasses__() blew the whole export up with
        NotImplementedError for anyone who had survived an item merge.
        """
        comment = Comment.objects.create(
            item=self.book1, owner=self.user1.identity, text="hi", visibility=0
        )
        Debris.create_from_piece(comment)

        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        with zipfile.ZipFile(exporter.metadata["file"]) as zf:
            journal = zf.read("journal.ndjson").decode()
        types = {json.loads(line)["type"] for line in journal.splitlines()[1:]}
        assert "Comment" in types
        assert "Debris" not in types

    def test_ndjson_catalog_covers_shelf_only_items(self):
        """A mark whose item has no comment/rating/log still round-trips.

        Only ShelfMember referenced such an item, and ShelfMember was the one
        piece type that never ref()'d its item into catalog.ndjson, so the
        mark failed to import with "Could not find item".
        """
        mark = Mark(self.user1.identity, self.book1)
        mark.update(ShelfType.WISHLIST, created_time=self.dt)
        mark.delete_all_logs()

        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        with zipfile.ZipFile(exporter.metadata["file"]) as zf:
            catalog = zf.read("catalog.ndjson").decode()
        assert self.book1.absolute_url in catalog

        importer = NdjsonImporter.create(
            user=self.user2, file=exporter.metadata["file"], visibility=0
        )
        importer.run()
        assert importer.metadata["failed"] == 0
        assert Mark(self.user2.identity, self.book1).shelf_type == ShelfType.WISHLIST

    def test_ndjson_rating_newer_import_updates_in_place(self):
        """(owner, item) is unique on Rating, so a newer import must update.

        Inserting a second row raised IntegrityError, which marks the
        surrounding transaction for rollback and fails every later record.
        """
        importer = NdjsonImporter.create(user=self.user2, file="x.zip", visibility=0)
        importer.items = {self.book1.absolute_url: self.book1}
        owner = self.user2.identity
        Rating.objects.create(
            item=self.book1, owner=owner, grade=5, visibility=0, created_time=self.dt
        )

        result = importer.import_rating(
            {
                "visibility": 0,
                "content": {
                    "withRegardTo": self.book1.absolute_url,
                    "value": 9,
                    "published": "2021-02-01T00:00:00Z",
                },
            }
        )
        assert result == "imported"
        rating = Rating.objects.get(owner=owner, item=self.book1)
        assert rating.grade == 9
        assert rating.created_time == self.dt2
        # the transaction is still usable — the old failure poisoned it
        assert Rating.objects.filter(owner=owner).count() == 1

    def test_ndjson_every_exported_type_has_an_importer(self):
        """The two sides must agree on every record type name.

        The exporter writes type "post" while the importer dispatched on
        "Post", so posts were counted in total but never processed and the
        progress percentage never reached 100%.
        """
        Takahe.post(self.user1.identity.pk, "hello world", Takahe.Visibilities.public)
        Mark(self.user1.identity, self.book1).update(
            ShelfType.COMPLETE, "done", 8, ["tagged"], 0, created_time=self.dt
        )
        Review.update_item_review(
            self.book1, self.user1.identity, "R", "body", visibility=0
        )
        Note.objects.create(
            item=self.book1, owner=self.user1.identity, content="n", visibility=0
        )
        Collection.objects.create(
            owner=self.user1.identity, title="C", brief="", visibility=0
        )
        Article.update_local_article(
            owner=self.user1.identity, title="A", body="b", visibility=0
        )

        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        export_path = exporter.metadata["file"]
        with zipfile.ZipFile(export_path) as zf:
            journal = zf.read("journal.ndjson").decode()
        exported_types = {
            json.loads(line)["type"]
            for line in journal.splitlines()[1:]
            if "type" in json.loads(line)
        }
        # covers every branch of the exporter, so a new record type added
        # without a matching handler fails here rather than in production
        assert exported_types >= {
            "Rating",
            "Comment",
            "Review",
            "Note",
            "Article",
            "Collection",
            "Tag",
            "TagMember",
            "ShelfMember",
            "ShelfLog",
            "post",
        }

        importer = NdjsonImporter.create(
            user=self.user2, file=export_path, visibility=0
        )
        assert exported_types <= set(importer.import_funcs())

        importer.run()
        m = importer.metadata
        assert m["total"] == exporter.metadata["total"]
        assert m["processed"] == m["total"]
        assert m["imported"] + m["skipped"] + m["failed"] == m["total"]

    def test_ndjson_unrecognised_records_are_counted(self, tmp_path):
        """A record type this version doesn't know still reaches the counters."""
        path = tmp_path / "journal.ndjson"
        path.write_text(
            json.dumps({"server": "x"})
            + "\n"
            + json.dumps({"type": "SomethingFromTheFuture"})
            + "\n"
        )
        importer = NdjsonImporter.create(user=self.user2, file="x.zip", visibility=0)
        importer.process_journal(str(path))
        assert importer.metadata["total"] == 1
        assert importer.metadata["processed"] == 1
        assert importer.metadata["skipped"] == 1

    def test_ndjson_review_inline_images_round_trip(self, tmp_path):
        """Inline images are bundled and the body repointed at the restored copy.

        Previously the exporter downloaded them into the archive but nothing
        mapped them back, so every local image 404'd after a server move.
        Images carrying alt text were not even bundled.
        """
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            buf = BytesIO()
            Image.new("RGB", (2, 2), "red").save(buf, format="PNG")
            name = default_storage.save(
                "upload/1/2026/pic.png", ContentFile(buf.getvalue())
            )
            src = default_storage.url(name)
            Review.update_item_review(
                self.book1,
                self.user1.identity,
                "Illustrated",
                f"before ![alt text]({src}) after",
                visibility=0,
            )

            exporter = NdjsonExporter.create(user=self.user1)
            exporter.run()
            export_path = exporter.metadata["file"]
            with zipfile.ZipFile(export_path) as zf:
                assert any(
                    n.startswith("attachments/") and n.endswith(".png")
                    for n in zf.namelist()
                )

            importer = NdjsonImporter.create(
                user=self.user2, file=export_path, visibility=0
            )
            importer.run()
            assert importer.metadata["failed"] == 0

            body = Review.objects.get(owner=self.user2.identity, item=self.book1).body
            assert src not in body, "body still points at the source server's media"
            new_src = body.split("![alt text](")[1].split(")")[0]
            assert new_src.startswith(settings.MEDIA_URL)
            assert default_storage.exists(new_src[len(settings.MEDIA_URL) :])

    def test_ndjson_progress_round_trips(self):
        """Reading progress survives export/import.

        ShelfMemberProgress was never exported and ShelfLogEntry.metadata
        (comment_text / rating_grade / progress_*) was dropped on both sides,
        so a restored journal lost its whole progress history.
        """
        mark = Mark(self.user1.identity, self.book1)
        mark.update(ShelfType.PROGRESS, "reading it", 7, [], 0, created_time=self.dt)
        mark.set_progress(Note.ProgressType.PAGE, "42")

        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        importer = NdjsonImporter.create(
            user=self.user2, file=exporter.metadata["file"], visibility=0
        )
        importer.run()
        assert importer.metadata["failed"] == 0

        imported = Mark(self.user2.identity, self.book1)
        assert imported.progress_type == Note.ProgressType.PAGE
        assert imported.progress_value == "42"

        def _logs(owner):
            return [
                (
                    log.shelf_type,
                    log.comment_text,
                    log.rating_grade,
                    log.progress_type,
                    log.progress_value,
                )
                for log in ShelfLogEntry.objects.filter(owner=owner).order_by(
                    "timestamp", "item_id"
                )
            ]

        assert _logs(self.user2.identity) == _logs(self.user1.identity)

    def test_ndjson_cleared_progress_round_trips(self):
        """A newer archive in which progress was cleared clears it on import.

        The exporter only emitted `progress` when a value existed, so a
        cleared mark looked exactly like a legacy archive and the
        destination silently kept stale progress.
        """
        # destination already holds progress for this item. set_progress
        # re-dates the mark to now, so wind it back explicitly — the archive
        # has to be the newer side for the import to touch the mark at all.
        dest_mark = Mark(self.user2.identity, self.book1)
        dest_mark.update(ShelfType.PROGRESS, created_time=self.dt)
        dest_mark.set_progress(Note.ProgressType.PAGE, "10")
        ShelfMember.objects.filter(owner=self.user2.identity, item=self.book1).update(
            created_time=self.dt
        )
        assert Mark(self.user2.identity, self.book1).progress_value == "10"

        # source is strictly newer and carries no progress
        src_mark = Mark(self.user1.identity, self.book1)
        src_mark.update(ShelfType.PROGRESS, created_time=self.dt2)

        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        with zipfile.ZipFile(exporter.metadata["file"]) as zf:
            journal = zf.read("journal.ndjson").decode()
        shelf_members = [
            json.loads(line)
            for line in journal.splitlines()[1:]
            if json.loads(line).get("type") == "ShelfMember"
        ]
        # the key is present-and-null, which is what makes clearing replayable
        assert "progress" in shelf_members[0]
        assert shelf_members[0]["progress"] is None

        importer = NdjsonImporter.create(
            user=self.user2, file=exporter.metadata["file"], visibility=0
        )
        importer.run()
        assert importer.metadata["failed"] == 0
        assert Mark(self.user2.identity, self.book1).progress_value is None

    def test_ndjson_legacy_archive_keeps_progress(self):
        """A record with no `progress` key must leave existing progress alone."""
        mark = Mark(self.user2.identity, self.book1)
        mark.update(ShelfType.PROGRESS, created_time=self.dt)
        mark.set_progress(Note.ProgressType.PAGE, "10")

        importer = NdjsonImporter.create(user=self.user2, file="x.zip", visibility=0)
        importer.restore_progress(self.user2.identity, self.book1, {})
        assert Mark(self.user2.identity, self.book1).progress_value == "10"

    def test_ndjson_bundles_absolute_local_media(self, tmp_path):
        """An absolute URL on our own site is copied from storage, not fetched.

        import_note records restored attachments as site_url + MEDIA_URL, so
        a re-export saw a URL that did not start with a relative MEDIA_URL
        and fell through to the HTTP downloader — which is_valid_url blocks
        for an internal host, silently dropping the file from the bundle.
        """
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            buf = BytesIO()
            Image.new("RGB", (2, 2), "green").save(buf, format="PNG")
            name = default_storage.save(
                "upload/1/2026/abs.png", ContentFile(buf.getvalue())
            )
            absolute = settings.SITE_INFO["site_url"].rstrip("/") + default_storage.url(
                name
            )
            assert not absolute.startswith(settings.MEDIA_URL)

            # a note whose media is only reachable through its stored
            # attachments JSON — exactly what import_note writes back
            note = Note.objects.create(
                item=self.book1,
                owner=self.user1.identity,
                content="see attached",
                visibility=0,
            )
            note.attachments = [
                {
                    "type": "image",
                    "mimetype": "image/png",
                    "url": absolute,
                    "preview_url": "",
                }
            ]
            note.save(
                update_fields=["attachments"],
                post_when_save=False,
                index_when_save=False,
            )

            exporter = NdjsonExporter.create(user=self.user1)
            exporter.run()
            with zipfile.ZipFile(exporter.metadata["file"]) as zf:
                names = zf.namelist()
                journal = zf.read("journal.ndjson").decode()
            assert any(
                n.startswith("attachments/") and n.endswith(".png") for n in names
            )
            record = next(
                r
                for r in (json.loads(line) for line in journal.splitlines()[1:])
                if r.get("type") == "Note"
            )
            assert record["attachments"][0]["file"].startswith("attachments/")

    def test_ndjson_shelf_log_empty_metadata_is_authoritative(self):
        """An explicit empty metadata overwrites; an absent key does not."""
        importer = NdjsonImporter.create(user=self.user2, file="x.zip", visibility=0)
        importer.items = {self.book1.absolute_url: self.book1}
        owner = self.user2.identity
        stale = ShelfLogEntry.objects.create(
            owner=owner,
            item=self.book1,
            shelf_type=ShelfType.COMPLETE,
            timestamp=self.dt,
            metadata={"comment_text": "stale", "rating_grade": 3},
        )
        record = {
            "item": self.book1.absolute_url,
            "status": ShelfType.COMPLETE,
            "timestamp": "2021-01-01T00:00:00Z",
        }

        # legacy archive (no metadata key): leave the row alone
        assert importer.import_shelf_log(record) == "imported"
        stale.refresh_from_db()
        assert stale.comment_text == "stale"

        # new archive that says the entry carries nothing: overwrite
        assert importer.import_shelf_log({**record, "metadata": {}}) == "imported"
        stale.refresh_from_db()
        assert stale.comment_text is None
        assert stale.rating_grade is None

    def test_ndjson_catalog_survives_bad_entries(self, tmp_path):
        """One unparseable catalog entry must not strand every later piece.

        parse_catalog only guarded json.loads, so a malformed
        external_resources entry raised out of the loop and aborted the
        whole catalog — every piece then failed with "Could not find item".
        """
        path = tmp_path / "catalog.ndjson"
        path.write_text(
            json.dumps({"server": "x"})
            + "\nnot json at all\n"
            + json.dumps(
                {
                    "id": "https://example.org/nowhere",
                    "external_resources": [{"missing_url_key": 1}],
                }
            )
            + "\n"
            + json.dumps({"id": self.book1.absolute_url})
            + "\n"
        )
        importer = NdjsonImporter.create(user=self.user2, file="x.zip", visibility=0)
        importer.parse_catalog(str(path))
        assert importer.items.get(self.book1.absolute_url) == self.book1

    @staticmethod
    def _write_catalog(path, *entries) -> None:
        """Write a catalog.ndjson holding `entries`, header included."""
        path.write_text(
            "\n".join([json.dumps({"server": "x"})] + [json.dumps(e) for e in entries])
            + "\n"
        )

    def _parse_catalog(self, tmp_path, *entries) -> NdjsonImporter:
        path = tmp_path / "catalog.ndjson"
        self._write_catalog(path, *entries)
        importer = NdjsonImporter.create(user=self.user2, file="x.zip", visibility=0)
        importer.parse_catalog(str(path))
        return importer

    def test_ndjson_catalog_rebuilds_item_from_bundled_metadata(self, tmp_path):
        """Every link failed, so the bundled entry itself becomes the item.

        The archive already carries each item's full metadata; without this,
        a source server that is gone (the .invalid host below never resolves)
        stranded every journal record referencing its items.
        """
        entry = {
            "id": "https://gone-rebuild.invalid/movie/aabbcc",
            "type": "Movie",
            "title": "A Lost Film",
            "localized_title": [{"lang": "en", "text": "A Lost Film"}],
            "localized_description": [{"lang": "en", "text": "Nowhere to fetch."}],
            "director": ["Someone"],
            "external_resources": [
                {"url": "https://gone-rebuild.invalid/movie/aabbcc"}
            ],
        }
        importer = self._parse_catalog(tmp_path, entry)
        item = importer.items[entry["id"]]
        assert isinstance(item, Movie)
        assert item.display_title == "A Lost Film"
        assert item.director == ["Someone"]

    def test_ndjson_catalog_rebuild_matches_existing_item_by_id(self, tmp_path):
        """The bundled ids dedup against this catalog instead of duplicating.

        parse_catalog passes no info string, so a bundled isbn/imdb used to be
        ignored entirely. Routed through the resource's lookup ids, an item
        already here is matched.
        """
        before = Edition.objects.count()
        entry = {
            "id": "https://gone-dedup.invalid/book/ddeeff",
            "type": "Edition",
            "localized_title": [{"lang": "en", "text": "Hyperion"}],
            "isbn": self.book1.isbn,
            "external_resources": [],
        }
        importer = self._parse_catalog(tmp_path, entry)
        assert importer.items[entry["id"]] == self.book1
        assert Edition.objects.count() == before

    def test_ndjson_catalog_rebuild_needs_sufficient_metadata(self, tmp_path):
        """Entries too thin to build from stay unresolved rather than half-built."""
        no_title = {"id": "https://gone-a.invalid/book/1", "type": "Edition"}
        unsupported = {
            "id": "https://gone-b.invalid/p/1",
            "type": "People",
            "localized_title": [{"lang": "en", "text": "Someone"}],
        }
        bad_url = {
            "id": "not a url at all",
            "type": "Edition",
            "localized_title": [{"lang": "en", "text": "Nowhere"}],
        }
        importer = self._parse_catalog(tmp_path, no_title, unsupported, bad_url)
        assert importer.items[no_title["id"]] is None
        assert importer.items[unsupported["id"]] is None
        assert importer.items[bad_url["id"]] is None

    def test_ndjson_catalog_rebuild_skips_local_urls(self, tmp_path):
        """A local url that did not resolve is a deleted item, not a new one."""
        before = Movie.objects.count()
        entry = {
            "id": settings.SITE_INFO["site_url"] + "/movie/nosuchitem",
            "type": "Movie",
            "localized_title": [{"lang": "en", "text": "Deleted Locally"}],
        }
        importer = self._parse_catalog(tmp_path, entry)
        assert importer.items[entry["id"]] is None
        assert Movie.objects.count() == before

    def test_ndjson_marks_import_against_a_rebuilt_item(self, tmp_path):
        """End to end: a mark on an unfetchable item still imports.

        This is the failure the rebuild exists to fix -- the mark used to be
        counted as failed with "Could not find item".
        """
        url = "https://gone-marks.invalid/book/xyz"
        catalog = tmp_path / "catalog.ndjson"
        self._write_catalog(
            catalog,
            {
                "id": url,
                "type": "Edition",
                "localized_title": [{"lang": "en", "text": "Vanished Volume"}],
                "author": ["Nobody"],
                "external_resources": [],
            },
        )
        journal = tmp_path / "journal.ndjson"
        journal.write_text(
            json.dumps({"server": "x"})
            + "\n"
            + json.dumps(
                {
                    "type": "ShelfMember",
                    "content": {
                        "withRegardTo": url,
                        "status": ShelfType.COMPLETE,
                        "published": "2021-01-01T00:00:00Z",
                    },
                    "visibility": 0,
                    "metadata": {},
                }
            )
            + "\n"
        )
        importer = NdjsonImporter.create(user=self.user2, file="x.zip", visibility=0)
        importer.parse_catalog(str(catalog))
        importer.process_journal(str(journal))
        assert importer.metadata["failed"] == 0
        assert importer.metadata["imported"] == 1
        item = importer.items[url]
        assert item is not None
        assert Mark(self.user2.identity, item).shelf_type == ShelfType.COMPLETE

    def test_ndjson_import_without_published_timestamp(self):
        """created_time is not nullable; a bundle without `published` must
        fall back to the model default instead of raising IntegrityError."""
        importer = NdjsonImporter.create(user=self.user2, file="x.zip", visibility=0)
        importer.items = {self.book1.absolute_url: self.book1}
        owner = self.user2.identity

        assert (
            importer.import_comment(
                {
                    "content": {
                        "withRegardTo": self.book1.absolute_url,
                        "content": "no timestamp",
                    }
                }
            )
            == "imported"
        )
        assert Comment.objects.get(owner=owner, item=self.book1).created_time

        assert (
            importer.import_collection(
                {"content": {"name": "No Timestamp"}, "items": []}
            )
            == "imported"
        )
        assert Collection.objects.get(owner=owner, title="No Timestamp").created_time

    # --- edits replay on re-import -------------------------------------

    def _roundtrip(self, source_user, dest_user):
        """Export ``source_user`` and import it into ``dest_user``."""
        exporter = NdjsonExporter.create(user=source_user)
        exporter.run()
        importer = NdjsonImporter.create(
            user=dest_user, file=exporter.metadata["file"], visibility=0
        )
        importer.run()
        return importer

    def test_ndjson_edit_replays_on_reimport(self):
        """An edit bumps only edited_time, so a created_time comparison reports
        every edited record as "not newer" and silently keeps the stale copy."""
        owner1 = self.user1.identity
        owner2 = self.user2.identity
        review = Review.objects.create(
            owner=owner1, item=self.book1, title="T", body="original", visibility=0
        )
        comment = Comment.objects.create(
            owner=owner1, item=self.book2, text="original", visibility=0
        )
        rating = Rating.objects.create(
            owner=owner1, item=self.movie1, grade=5, visibility=0
        )
        self._roundtrip(self.user1, self.user2)
        assert Review.objects.get(owner=owner2, item=self.book1).body == "original"
        assert Comment.objects.get(owner=owner2, item=self.book2).text == "original"
        assert Rating.objects.get(owner=owner2, item=self.movie1).grade == 5

        # edit on the source: created_time is untouched, edited_time moves.
        # the title is part of the review's identity, so it stays put here --
        # a retitle is covered separately.
        review.body = "edited"
        review.save()
        comment.text = "edited"
        comment.save()
        rating.grade = 9
        rating.save()

        self._roundtrip(self.user1, self.user2)
        assert Review.objects.filter(owner=owner2, item=self.book1).count() == 1
        assert Review.objects.get(owner=owner2, item=self.book1).body == "edited"
        assert Comment.objects.get(owner=owner2, item=self.book2).text == "edited"
        assert Rating.objects.get(owner=owner2, item=self.movie1).grade == 9

    def test_ndjson_note_and_collection_edits_replay_on_reimport(self):
        owner1 = self.user1.identity
        owner2 = self.user2.identity
        note = Note.objects.create(
            owner=owner1,
            item=self.book1,
            title="N",
            content="original",
            visibility=0,
            created_time=self.dt,
        )
        collection = Collection.objects.create(
            owner=owner1, title="C", brief="original", visibility=0
        )
        collection.append_item(self.book1, metadata={"note": "original"})
        self._roundtrip(self.user1, self.user2)
        assert Note.objects.get(owner=owner2, item=self.book1).content == "original"
        assert Collection.objects.get(owner=owner2, title="C").brief == "original"

        note.content = "edited"
        note.save()
        collection.brief = "edited"
        collection.save()
        member = collection.get_member_for_item(self.book1)
        member.note = "edited"
        member.save()

        self._roundtrip(self.user1, self.user2)
        assert Note.objects.filter(owner=owner2, item=self.book1).count() == 1
        assert Note.objects.get(owner=owner2, item=self.book1).content == "edited"
        assert Collection.objects.filter(owner=owner2).count() == 1
        c2 = Collection.objects.get(owner=owner2)
        assert c2.brief == "edited"
        # per-item notes replay too; append_item alone is a no-op for a member
        # already on the list
        assert c2.get_member_for_item(self.book1).note == "edited"

    def test_ndjson_article_edit_and_language_round_trip(self):
        owner1 = self.user1.identity
        owner2 = self.user2.identity
        article = Article.update_local_article(
            owner=owner1, title="A", body="original", language="fr", visibility=0
        )
        self._roundtrip(self.user1, self.user2)
        imported = Article.objects.get(owner=owner2)
        assert imported.body == "original"
        # language is not part of ap_object, so it rides the NDJSON envelope
        assert imported.language == "fr"

        # edit through the real edit path: Article.edited_time is not
        # auto_now, and update_local_article is what advances it
        Article.update_local_article(
            owner=owner1,
            title="A",
            body="edited",
            language="fr",
            visibility=0,
            article=article,
        )

        self._roundtrip(self.user1, self.user2)
        assert Article.objects.filter(owner=owner2).count() == 1
        assert Article.objects.get(owner=owner2).body == "edited"

    def test_ndjson_unedited_reimport_still_skips(self):
        """The staleness guard must not turn every re-import into a rewrite."""
        owner2 = self.user2.identity
        Review.objects.create(
            owner=self.user1.identity,
            item=self.book1,
            title="T",
            body="original",
            visibility=0,
        )
        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        path = exporter.metadata["file"]
        NdjsonImporter.create(user=self.user2, file=path, visibility=0).run()
        importer = NdjsonImporter.create(user=self.user2, file=path, visibility=0)
        importer.run()
        assert Review.objects.filter(owner=owner2, item=self.book1).count() == 1
        assert importer.metadata["skipped"] >= 1

    # --- other fixes --------------------------------------------------

    def test_ndjson_export_excludes_deleted_posts(self):
        """Takahe keeps deleted rows for up to 24h, so an unfiltered query
        bundles posts the user already deleted."""
        post = Takahe.post(
            self.user1.identity.pk, "a plain post", Takahe.Visibilities.public
        )
        assert post
        deleted = Takahe.post(
            self.user1.identity.pk, "a deleted post", Takahe.Visibilities.public
        )
        assert deleted
        Takahe.delete_posts([deleted.pk])

        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        with zipfile.ZipFile(exporter.metadata["file"]) as z:
            lines = z.read("journal.ndjson").decode().strip().split("\n")
        posts = [
            json.loads(ln) for ln in lines[1:] if json.loads(ln).get("type") == "post"
        ]
        contents = " ".join(p["post"].get("content", "") for p in posts)
        assert "a plain post" in contents
        assert "a deleted post" not in contents

    def test_ndjson_tag_member_without_published_timestamp(self):
        """created_time is not nullable; passing None raises IntegrityError and
        poisons the transaction for every later record."""
        importer = NdjsonImporter.create(user=self.user2, file="x.zip", visibility=0)
        importer.items = {self.book1.absolute_url: self.book1}
        assert (
            importer.import_tag_member(
                {"content": {"withRegardTo": self.book1.absolute_url, "tag": "notime"}}
            )
            == "imported"
        )
        tag = Tag.objects.get(owner=self.user2.identity, title="notime")
        assert tag.created_time
        assert TagMember.objects.get(owner=self.user2.identity, parent=tag).created_time

    def test_ndjson_validate_file(self):
        """format_type is chosen client-side, so the file itself is checked."""
        assert not NdjsonImporter.validate_file(
            SimpleUploadedFile("x.zip", b"not a zip at all")
        )
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("marks.csv", "a,b\n")
        assert not NdjsonImporter.validate_file(
            SimpleUploadedFile("x.zip", buf.getvalue())
        )
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("journal.ndjson", '{"server": "x"}\n')
        f = SimpleUploadedFile("x.zip", buf.getvalue())
        assert NdjsonImporter.validate_file(f)
        # left rewound so the view can still write the upload out
        assert f.tell() == 0

    def test_ndjson_retitle_reimports_as_a_second_row(self):
        """Known limitation: the title is part of a Review's / Collection's /
        Article's identity here, so a retitle on the source cannot be told
        apart from a new piece and lands as a second row. Keying on
        created_time instead would resolve the retitle but silently overwrite a
        genuinely different piece that shares a timestamp, which is worse."""
        owner1 = self.user1.identity
        owner2 = self.user2.identity
        review = Review.objects.create(
            owner=owner1, item=self.book1, title="T", body="body", visibility=0
        )
        self._roundtrip(self.user1, self.user2)
        assert Review.objects.filter(owner=owner2, item=self.book1).count() == 1

        review.title = "T-renamed"
        review.save()
        self._roundtrip(self.user1, self.user2)
        assert Review.objects.filter(owner=owner2, item=self.book1).count() == 2

    # --- codex review follow-ups ---------------------------------------

    def test_ndjson_skipped_collection_does_not_restore_images(self, tmp_path):
        """A no-op re-import must not copy bundled images into storage again.

        Restoring the body writes each image into media storage, so doing it
        before the staleness check left a fresh copy behind on every re-import
        of an unchanged collection.
        """
        with override_settings(MEDIA_ROOT=str(tmp_path)):
            buf = BytesIO()
            Image.new("RGB", (2, 2), "blue").save(buf, format="PNG")
            name = default_storage.save(
                "upload/1/2026/coll.png", ContentFile(buf.getvalue())
            )
            src = default_storage.url(name)
            Collection.objects.create(
                owner=self.user1.identity,
                title="Illustrated Collection",
                brief=f"before ![alt]({src}) after",
                visibility=0,
            )
            exporter = NdjsonExporter.create(user=self.user1)
            exporter.run()
            path = exporter.metadata["file"]

            def stored_uploads():
                return sorted(default_storage.listdir("upload")[0])

            NdjsonImporter.create(user=self.user2, file=path, visibility=0).run()
            after_first = stored_uploads()
            # second import is a no-op: nothing new may be written
            importer = NdjsonImporter.create(user=self.user2, file=path, visibility=0)
            importer.run()
            assert stored_uploads() == after_first
            assert (
                Collection.objects.filter(
                    owner=self.user2.identity, title="Illustrated Collection"
                ).count()
                == 1
            )

    def test_ndjson_member_note_edit_reindexes_collection(self):
        """The collection's index doc embeds member notes, and saving a member
        does not reindex its parent -- only list_add / list_remove do, via
        signal, and a note edit fires neither.

        The brief is edited alongside the note because a member note on its own
        leaves the collection's edited_time untouched, so the record is judged
        current and skipped before the member loop runs.
        """
        index = JournalIndex.instance()
        index.delete_all()
        owner1 = self.user1.identity
        collection = Collection.objects.create(
            owner=owner1, title="Indexed", brief="b", visibility=0
        )
        collection.append_item(self.book1, metadata={"note": "first note"})
        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        NdjsonImporter.create(
            user=self.user2, file=exporter.metadata["file"], visibility=0
        ).run()

        member = collection.get_member_for_item(self.book1)
        member.note = "second note"
        member.save()
        collection.brief = "b2"
        collection.save()
        exporter2 = NdjsonExporter.create(user=self.user1)
        exporter2.run()
        NdjsonImporter.create(
            user=self.user2, file=exporter2.metadata["file"], visibility=0
        ).run()

        dest = Collection.objects.get(owner=self.user2.identity, title="Indexed")
        assert dest.get_member_for_item(self.book1).note == "second note"
        # assert against the stored document, not to_indexable_doc(), which
        # recomputes from the database and so cannot see a stale index
        # same id piece_to_doc builds: the post id when the piece has one
        doc_id = str(dest.latest_post_id) if dest.latest_post_id else f"p{dest.pk}"
        stored = index.get_doc(doc_id)
        assert stored, "collection has no index document"
        assert "second note" in stored["content"]
        assert "first note" not in stored["content"]

    def test_validate_file_matches_what_the_importers_read(self):
        """Validation keys on the member paths run() actually opens, so an
        archive that would fail (or import nothing) is rejected up front."""

        def zipped(*names):
            buf = BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for n in names:
                    z.writestr(n, "x")
            return SimpleUploadedFile("x.zip", buf.getvalue())

        # ndjson: run() opens <tempdir>/journal.ndjson, nothing nested
        assert NdjsonImporter.validate_file(zipped("journal.ndjson"))
        assert not NdjsonImporter.validate_file(zipped("nested/journal.ndjson"))

        # and the validator must accept what our own exporter produces
        exporter = NdjsonExporter.create(user=self.user1)
        exporter.run()
        with open(exporter.metadata["file"], "rb") as f:
            assert NdjsonImporter.validate_file(
                SimpleUploadedFile("export.zip", f.read())
            )

        # csv: run() reads only <category>_{mark,review,note}.csv at the root
        assert CsvImporter.validate_file(zipped("movie_mark.csv"))
        assert CsvImporter.validate_file(zipped("book_review.csv"))
        assert not CsvImporter.validate_file(zipped("foo.csv"))
        assert not CsvImporter.validate_file(zipped("nested/movie_mark.csv"))
