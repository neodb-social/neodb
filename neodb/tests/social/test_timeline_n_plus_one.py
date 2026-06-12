"""Tests for N+1 query optimizations in the timeline data view."""

import pytest
from django.db import connections
from django.test import Client
from django.test.utils import CaptureQueriesContext

from catalog.models import Edition
from journal.models import Mark, ShelfType
from users.models import User

NUM_ITEMS = 5


@pytest.mark.django_db(databases="__all__")
class TestTimelineDataNPlusOne:
    """Test that /timeline/data avoids N+1 queries for pieces, items, domains, and mentions."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(
            email="timeline_npo@example.com", username="timeliner"
        )
        self.books = [
            Edition.objects.create(title=f"TL Book {i}") for i in range(NUM_ITEMS)
        ]
        for i, book in enumerate(self.books):
            Mark(self.user.identity, book).update(
                ShelfType.WISHLIST, f"note {i}", i + 5, visibility=0
            )
        self.client = Client()
        self.client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")

    def test_no_per_post_piece_queries(self):
        """Piece lookups should be batched, not one query per post."""
        with CaptureQueriesContext(connections["default"]) as ctx:
            response = self.client.get("/timeline/data")
        assert response.status_code == 200
        # Individual PiecePost lookups have "post_id" = %s (single value, no IN)
        individual_piecepost = [
            q
            for q in ctx.captured_queries
            if "journal_piecepost" in q["sql"]
            and "post_id" in q["sql"]
            and "IN" not in q["sql"].upper()
        ]
        assert len(individual_piecepost) == 0, (
            f"Expected 0 individual PiecePost queries, got {len(individual_piecepost)}: "
            + "; ".join(q["sql"][:120] for q in individual_piecepost)
        )

    def test_no_per_post_domain_queries(self):
        """Author domain should be select_related, not queried per post."""
        with CaptureQueriesContext(connections["takahe"]) as ctx:
            response = self.client.get("/timeline/data")
        assert response.status_code == 200
        # Individual domain lookups: WHERE "users_domain"."domain" = %s
        individual_domain = [
            q
            for q in ctx.captured_queries
            if "users_domain" in q["sql"]
            and 'WHERE "users_domain"."domain"' in q["sql"]
        ]
        assert len(individual_domain) == 0, (
            f"Expected 0 individual domain queries, got {len(individual_domain)}: "
            + "; ".join(q["sql"][:120] for q in individual_domain)
        )

    def test_no_per_post_mention_queries(self):
        """Post mentions should be prefetch_related, not queried per post."""
        with CaptureQueriesContext(connections["takahe"]) as ctx:
            response = self.client.get("/timeline/data")
        assert response.status_code == 200
        # Individual mention lookups: activities_post_mentions with single post_id
        individual_mentions = [
            q
            for q in ctx.captured_queries
            if "activities_post_mentions" in q["sql"] and "IN" not in q["sql"].upper()
        ]
        assert len(individual_mentions) == 0, (
            f"Expected 0 individual mention queries, got {len(individual_mentions)}: "
            + "; ".join(q["sql"][:120] for q in individual_mentions)
        )

    def test_no_per_item_card_data_queries(self):
        """Item-card data must be batch-prefetched in prefetch_pieces_for_posts,
        not queried once per feed item while rendering feed_events.html
        (NEODB-SOCIAL-4QY, NEODB-SOCIAL-7MQ).

        external_resources and credits are read only by the item-card partials,
        so a single-item lookup (``"item_id" = ...`` rather than the batched
        ``"item_id" IN (...)``) on those tables is an unambiguous N+1 signal.
        """
        from catalog.models import ExternalResource, IdType

        for i, book in enumerate(self.books):
            ExternalResource.objects.create(
                item=book,
                id_type=IdType.GoogleBooks,
                id_value=f"npo-{i}",
                url=f"https://books.google.com/books?id=npo-{i}",
            )
        with CaptureQueriesContext(connections["default"]) as ctx:
            response = self.client.get("/timeline/data")
        assert response.status_code == 200

        per_item_signals = {
            "catalog_externalresource": '"catalog_externalresource"."item_id" =',
            "catalog_itemcredit": '"catalog_itemcredit"."item_id" =',
        }
        for table, needle in per_item_signals.items():
            offenders = [q for q in ctx.captured_queries if needle in q["sql"]]
            assert len(offenders) == 0, (
                f"Expected 0 per-item {table} queries, got {len(offenders)}: "
                + "; ".join(q["sql"][:120] for q in offenders)
            )

    def test_no_deferred_external_resource_metadata_load(self):
        """Album feed cards render with allow_embed=1, so Album.get_embed_link
        reads ExternalResource.metadata. The feed prefetch must keep metadata in
        the loaded column set; otherwise each Bandcamp resource triggers a
        deferred ``WHERE "catalog_externalresource"."id" = ...`` load per card.
        """
        from catalog.models import Album, ExternalResource, IdType

        album = Album.objects.create(title="TL Album")
        ExternalResource.objects.create(
            item=album,
            id_type=IdType.Bandcamp,
            id_value="bc-1",
            url="https://artist.bandcamp.com/album/tl",
            metadata={"bandcamp_album_id": "123456"},
        )
        Mark(self.user.identity, album).update(
            ShelfType.COMPLETE, "great album", 9, visibility=0
        )
        with CaptureQueriesContext(connections["default"]) as ctx:
            response = self.client.get("/timeline/data")
        assert response.status_code == 200
        # A deferred metadata load fetches a single resource by primary key.
        deferred = [
            q
            for q in ctx.captured_queries
            if 'FROM "catalog_externalresource"' in q["sql"]
            and '"catalog_externalresource"."id" =' in q["sql"]
        ]
        assert len(deferred) == 0, (
            f"Expected 0 deferred ExternalResource loads, got {len(deferred)}: "
            + "; ".join(q["sql"][:120] for q in deferred)
        )

    def test_query_count_stable_with_more_items(self):
        """Adding more items should not proportionally increase query count.

        Without batch-fetching, doubling posts from 5 to 10 would add ~20
        extra queries (4 N+1 patterns x 5 new posts). With batch-fetching
        the growth is much smaller -- mainly from django-polymorphic content
        type resolution.
        """
        # Measure baseline with current items (5 posts)
        with CaptureQueriesContext(connections["default"]) as ctx_default:
            with CaptureQueriesContext(connections["takahe"]) as ctx_takahe:
                response = self.client.get("/timeline/data")
        assert response.status_code == 200
        baseline_default = len(ctx_default.captured_queries)
        baseline_takahe = len(ctx_takahe.captured_queries)

        # Add more items (total 10 posts, PAGE_SIZE)
        extra_books = [
            Edition.objects.create(title=f"TL Extra {i}") for i in range(NUM_ITEMS)
        ]
        for i, book in enumerate(extra_books):
            Mark(self.user.identity, book).update(
                ShelfType.COMPLETE, f"extra {i}", i + 3, visibility=0
            )

        # Measure with double the items
        with CaptureQueriesContext(connections["default"]) as ctx_default2:
            with CaptureQueriesContext(connections["takahe"]) as ctx_takahe2:
                response = self.client.get("/timeline/data")
        assert response.status_code == 200

        # Query counts should not grow proportionally to the number of posts.
        # Allow headroom for polymorphic content-type resolution queries.
        assert len(ctx_default2.captured_queries) <= baseline_default + 15, (
            f"Default DB queries grew from {baseline_default} to "
            f"{len(ctx_default2.captured_queries)} after doubling items"
        )
        assert len(ctx_takahe2.captured_queries) <= baseline_takahe + 5, (
            f"Takahe DB queries grew from {baseline_takahe} to "
            f"{len(ctx_takahe2.captured_queries)} after doubling items"
        )
