"""Tests for N+1 query optimizations."""

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from catalog.models import Edition, ExternalResource, IdType, Movie
from journal.models import Collection, Mark, ShelfType, Tag
from journal.models.common import prefetch_pieces_for_posts
from journal.models.shelf import ShelfMember
from takahe.utils import Takahe
from users.models import User


@pytest.mark.django_db(databases="__all__")
class TestTagManagerGetItemsTags:
    """Test the batch tag fetching method."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="tags@example.com", username="taguser")
        self.book1 = Edition.objects.create(title="Book 1")
        self.book2 = Edition.objects.create(title="Book 2")
        self.book3 = Edition.objects.create(title="Book 3")

        tag_a = Tag.objects.create(
            owner=self.user.identity, title="fiction", visibility=0
        )
        tag_b = Tag.objects.create(
            owner=self.user.identity, title="sci-fi", visibility=0
        )
        tag_a.append_item(self.book1)
        tag_a.append_item(self.book2)
        tag_b.append_item(self.book1)

    def test_batch_returns_correct_tags(self):
        tm = self.user.identity.tag_manager
        result = tm.get_items_tags([self.book1.pk, self.book2.pk, self.book3.pk])
        assert sorted(result[self.book1.pk]) == ["fiction", "sci-fi"]
        assert result[self.book2.pk] == ["fiction"]
        assert result[self.book3.pk] == []

    def test_batch_matches_individual(self):
        tm = self.user.identity.tag_manager
        batch = tm.get_items_tags([self.book1.pk, self.book2.pk])
        individual1 = tm.get_item_tags(self.book1)
        individual2 = tm.get_item_tags(self.book2)
        assert sorted(batch[self.book1.pk]) == sorted(individual1)
        assert sorted(batch[self.book2.pk]) == sorted(individual2)

    def test_empty_list(self):
        tm = self.user.identity.tag_manager
        result = tm.get_items_tags([])
        assert result == {}

    def test_single_query(self):
        """Batch method should use a single query regardless of item count."""
        tm = self.user.identity.tag_manager
        item_ids = [self.book1.pk, self.book2.pk, self.book3.pk]
        with CaptureQueriesContext(connection) as ctx:
            tm.get_items_tags(item_ids)
        tag_queries = [q for q in ctx.captured_queries if "journal_tag" in q["sql"]]
        assert len(tag_queries) == 1


@pytest.mark.django_db(databases="__all__")
class TestShelfMemberTagsProperty:
    """Test that ShelfMember.tags uses pre-set _tags when available."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="shelf@example.com", username="shelfuser")
        self.book = Edition.objects.create(title="Shelf Book")
        shelf = self.user.identity.shelf_manager.get_shelf(ShelfType.WISHLIST)
        self.member = ShelfMember.objects.create(
            owner=self.user.identity,
            item=self.book,
            parent=shelf,
            visibility=0,
            position=0,
        )

    def test_preset_tags_used(self):
        self.member._tags = ["preset-tag-1", "preset-tag-2"]
        assert self.member.tags == ["preset-tag-1", "preset-tag-2"]

    def test_fallback_to_mark_tags(self):
        """Without _tags, should fall back to mark.tags."""
        tag = Tag.objects.create(
            owner=self.user.identity, title="real-tag", visibility=0
        )
        tag.append_item(self.book)
        member = ShelfMember.objects.get(pk=self.member.pk)
        assert member.tags == ["real-tag"]


@pytest.mark.django_db(databases="__all__")
class TestMarkBatchFetch:
    """Test Mark.get_marks_by_items batch fetching."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="mark@example.com", username="markuser")
        self.books = [Edition.objects.create(title=f"Book {i}") for i in range(5)]
        # Mark some books
        Mark(self.user.identity, self.books[0]).update(
            ShelfType.WISHLIST, "comment0", 7, ["tag0"], 0
        )
        Mark(self.user.identity, self.books[1]).update(
            ShelfType.COMPLETE, "comment1", 8, ["tag1"], 0
        )
        Mark(self.user.identity, self.books[2]).update(
            ShelfType.PROGRESS, None, None, [], 0
        )

    def test_batch_marks_match_individual(self):
        marks = Mark.get_marks_by_items(self.user.identity, self.books, self.user)
        assert len(marks) == 5
        assert marks[self.books[0].pk].shelf_type == ShelfType.WISHLIST
        assert marks[self.books[0].pk].comment_text == "comment0"
        assert marks[self.books[0].pk].rating_grade == 7
        assert marks[self.books[0].pk].tags == ["tag0"]
        assert marks[self.books[1].pk].shelf_type == ShelfType.COMPLETE
        assert marks[self.books[2].pk].shelf_type == ShelfType.PROGRESS
        assert marks[self.books[3].pk].shelfmember is None
        assert marks[self.books[4].pk].shelfmember is None

    def test_bounded_queries(self):
        """Batch fetch should use bounded number of queries."""
        with CaptureQueriesContext(connection) as ctx:
            Mark.get_marks_by_items(self.user.identity, self.books, self.user)
        # Should be bounded: ShelfMember + Comment + Rating + Review + Notes + TagMember
        # Plus polymorphic model resolution queries (Django polymorphic adds extra queries)
        # Not proportional to number of items (5 items)
        assert len(ctx.captured_queries) <= 30


@pytest.mark.django_db(databases="__all__")
class TestRenderListPrefetch:
    """Test that render_list batch-fetches marks for list members."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="list@example.com", username="listuser")
        self.books = [Edition.objects.create(title=f"List Book {i}") for i in range(3)]
        self.tag = Tag.objects.create(
            owner=self.user.identity, title="test-tag", visibility=0
        )
        for book in self.books:
            Mark(self.user.identity, book).update(
                ShelfType.WISHLIST, f"comment for {book.title}", 7, ["test-tag"], 0
            )
            self.tag.append_item(book)

    def test_tag_list_page_renders(self):
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(
            f"/users/{self.user.username}/tags/test-tag/",
        )
        assert response.status_code == 200
        for book in self.books:
            assert book.display_title in response.content.decode()

    def test_tag_list_bounded_queries(self):
        """Tag list should use bounded queries, not N+1 per item."""
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                f"/users/{self.user.username}/tags/test-tag/",
            )
        assert response.status_code == 200
        # Count queries that would be N+1 if not batched:
        # per-item ShelfMember, Rating, Comment, Review, Tag, ExternalResource
        shelfmember_queries = [
            q
            for q in ctx.captured_queries
            if "journal_shelfmember" in q["sql"]
            and "WHERE" in q["sql"]
            and "item_id" in q["sql"]
        ]
        # With batch fetch, shelfmember lookups should be 1 (IN query), not N
        assert len(shelfmember_queries) <= 2

    def test_shelf_list_page_renders(self):
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(
            f"/users/{self.user.username}/wishlist/book/",
        )
        assert response.status_code == 200


@pytest.mark.django_db(databases="__all__")
class TestCommentsPrefetch:
    """Test that comments view prefetches related data."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user1 = User.register(email="c1@example.com", username="commenter1")
        self.user2 = User.register(email="c2@example.com", username="commenter2")
        self.book = Edition.objects.create(title="Comment Book")

        # Create marks with comments from two users
        Mark(self.user1.identity, self.book).update(
            ShelfType.COMPLETE, "Great book!", 9, [], 0
        )
        Mark(self.user2.identity, self.book).update(
            ShelfType.PROGRESS, "Still reading", None, [], 0
        )

    def test_comments_page_renders(self):
        client = Client()
        response = client.get(
            f"/book/{self.book.uuid}/comments",
        )
        assert response.status_code == 200

    def test_comments_bounded_queries(self):
        client = Client()
        client.force_login(self.user1, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                f"/book/{self.book.uuid}/comments",
            )
        assert response.status_code == 200
        # Should NOT have per-comment ShelfMember queries
        # The batch fetch uses IN queries instead
        shelfmember_queries = [
            q
            for q in ctx.captured_queries
            if "journal_shelfmember" in q["sql"]
            and "owner_id" in q["sql"]
            and "item_id" in q["sql"]
        ]
        # With batch fetch: should have at most 1 IN query, not N individual queries
        assert len(shelfmember_queries) <= 2


@pytest.mark.django_db(databases="__all__")
class TestShelfAPIPrefetch:
    """Test that shelf API batch-fetches data."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        from takahe.utils import Takahe

        self.user = User.register(email="api@example.com", username="apiuser")
        self.books = [Edition.objects.create(title=f"API Book {i}") for i in range(3)]
        for i, book in enumerate(self.books):
            Mark(self.user.identity, book).update(
                ShelfType.WISHLIST, f"note {i}", i + 5, [f"tag{i}"], 0
            )
            ExternalResource.objects.create(
                item=book,
                id_type=IdType.ISBN,
                id_value=f"978000000000{i}",
                url=f"https://example.com/book/{i}",
            )
        self.app = Takahe.get_or_create_app(
            "Test",
            "https://example.org",
            "https://example.org/cb",
            owner_pk=self.user.identity.pk,
        )
        self.token = Takahe.refresh_token(self.app, self.user.identity.pk, self.user.pk)

    def test_shelf_api_returns_data(self):
        client = Client()
        response = client.get(
            "/api/me/shelf/wishlist",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert len(data) == 3
        # Verify tags are present
        all_tags = {tag for item in data for tag in item["tags"]}
        assert all_tags == {"tag0", "tag1", "tag2"}

    def test_shelf_api_bounded_queries(self):
        client = Client()
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                "/api/me/shelf/wishlist",
                HTTP_AUTHORIZATION=f"Bearer {self.token}",
            )
        assert response.status_code == 200
        # With batch: at most 1 IN query for tags, not N individual queries
        tag_member_individual = [
            q
            for q in ctx.captured_queries
            if "journal_tagmember" in q["sql"]
            and "item_id" in q["sql"]
            and "IN" not in q["sql"]
        ]
        assert len(tag_member_individual) == 0


@pytest.mark.django_db(databases="__all__")
class TestPrefetchShelfMembers:
    """Test the _prefetch_shelf_members helper."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="pfx@example.com", username="pfxuser")
        self.book = Edition.objects.create(title="Prefetch Book")
        self.movie = Movie.objects.create(title="Prefetch Movie")
        shelf = self.user.identity.shelf_manager.get_shelf(ShelfType.WISHLIST)
        self.sm1 = ShelfMember.objects.create(
            owner=self.user.identity,
            item=self.book,
            parent=shelf,
            visibility=0,
            position=0,
        )
        self.sm2 = ShelfMember.objects.create(
            owner=self.user.identity,
            item=self.movie,
            parent=shelf,
            visibility=0,
            position=0,
        )
        Tag.objects.create(
            owner=self.user.identity, title="prefetch-tag", visibility=0
        ).append_item(self.book)

    def test_prefetch_sets_tags(self):
        from journal.apis.shelf import _prefetch_shelf_members

        members = list(
            ShelfMember.objects.filter(owner=self.user.identity).prefetch_related(
                "item"
            )
        )
        _prefetch_shelf_members(members)
        tags_map = {m.item_id: m.tags for m in members}
        assert tags_map[self.book.pk] == ["prefetch-tag"]
        assert tags_map[self.movie.pk] == []

    def test_prefetch_sets_rating_info(self):
        from journal.apis.shelf import _prefetch_shelf_members

        members = list(
            ShelfMember.objects.filter(owner=self.user.identity).prefetch_related(
                "item"
            )
        )
        _prefetch_shelf_members(members)
        for m in members:
            assert hasattr(m.item, "rating_info")


@pytest.mark.django_db(databases="__all__")
class TestPrefetchPiecesForPosts:
    """Test batch prefetching of pieces and items for posts."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="prefetch@example.com", username="prefetchuser")
        self.book = Edition.objects.create(title="Prefetch Book")
        self.movie = Movie.objects.create(title="Prefetch Movie")
        Mark(self.user.identity, self.book).update(
            ShelfType.WISHLIST, "want to read", visibility=0
        )
        Mark(self.user.identity, self.movie).update(
            ShelfType.COMPLETE, "great film", 8, visibility=0
        )

    def test_prefetch_sets_piece_and_item(self):
        posts = list(Takahe.get_recent_posts(self.user.identity.pk)[:10])
        assert len(posts) >= 2
        prefetch_pieces_for_posts(posts)
        for post in posts:
            assert "piece" in post.__dict__
            assert "item" in post.__dict__
            assert post.piece is not None
            assert post.item is not None

    def test_prefetch_avoids_per_post_queries(self):
        """After prefetch, accessing piece/item should not trigger new queries."""
        posts = list(Takahe.get_recent_posts(self.user.identity.pk)[:10])
        prefetch_pieces_for_posts(posts)
        # All pieces and items are now cached in __dict__; no more DB hits
        with CaptureQueriesContext(connection) as ctx:
            for post in posts:
                _ = post.piece
                _ = post.item
        assert len(ctx.captured_queries) == 0

    def test_prefetch_empty_list(self):
        prefetch_pieces_for_posts([])  # should not raise


@pytest.mark.django_db(databases="__all__")
class TestCommentLatestPostPrefetch:
    """Test that comments prefetch avoids N+1 on latest_post and post.author."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.users = [
            User.register(email=f"clp{i}@example.com", username=f"clpuser{i}")
            for i in range(5)
        ]
        self.book = Edition.objects.create(title="Post Prefetch Book")
        for i, user in enumerate(self.users):
            Mark(user.identity, self.book).update(
                ShelfType.COMPLETE, f"comment {i}", i + 5, [], 0
            )

    def test_comments_no_per_comment_identity_queries(self):
        """Comments page should not query users_identity per comment."""
        client = Client()
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(f"/book/{self.book.uuid}/comments")
        assert response.status_code == 200
        # Count individual identity lookups (N+1 pattern)
        identity_queries = [
            q
            for q in ctx.captured_queries
            if "users_identity" in q["sql"]
            and 'WHERE "users_identity"."id"' in q["sql"]
        ]
        # With batch prefetch, should have 0 individual identity queries
        # (authors are prefetched via Takahe.get_posts)
        assert len(identity_queries) == 0

    def test_comments_no_per_comment_post_queries(self):
        """Comments page should not query posts individually per comment."""
        client = Client()
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(f"/book/{self.book.uuid}/comments")
        assert response.status_code == 200
        # Count individual post lookups
        post_queries = [
            q
            for q in ctx.captured_queries
            if "activities_post" in q["sql"]
            and 'WHERE "activities_post"."id"' in q["sql"]
        ]
        # With batch prefetch, should have 0 individual post queries
        assert len(post_queries) == 0

    def test_comments_no_per_comment_piecepost_queries(self):
        """Comments page should batch PiecePost queries."""
        client = Client()
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(f"/book/{self.book.uuid}/comments")
        assert response.status_code == 200
        piecepost_queries = [
            q
            for q in ctx.captured_queries
            if "journal_piecepost" in q["sql"]
            and "piece_id" in q["sql"]
            and "IN" not in q["sql"].upper()
        ]
        # Should have 0 individual piecepost queries (all batched via IN)
        assert len(piecepost_queries) == 0


@pytest.mark.django_db(databases="__all__")
class TestCollectionEditItemsPrefetch:
    """Test that collection edit_items view batch-fetches items."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="cedit@example.com", username="cedituser")
        self.books = [
            Edition.objects.create(title=f"Coll Edit Book {i}") for i in range(5)
        ]
        for book in self.books:
            ExternalResource.objects.create(
                item=book,
                id_type=IdType.ISBN,
                id_value=f"978111100000{book.pk}",
                url=f"https://example.com/book/{book.pk}",
            )
        self.collection = Collection.objects.create(
            title="Edit Test Collection",
            owner=self.user.identity,
        )
        for book in self.books:
            self.collection.append_item(book)

    def test_edit_items_renders(self):
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(
            f"/collection/{self.collection.uuid}/edit_items",
        )
        assert response.status_code == 200
        for book in self.books:
            assert book.display_title in response.content.decode()

    def test_edit_items_no_per_item_queries(self):
        """Edit items should batch-fetch items, not query per member."""
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                f"/collection/{self.collection.uuid}/edit_items",
            )
        assert response.status_code == 200
        # Should NOT have individual catalog_item lookups per member
        individual_item_queries = [
            q
            for q in ctx.captured_queries
            if "catalog_item" in q["sql"] and 'WHERE "catalog_item"."id" =' in q["sql"]
        ]
        assert len(individual_item_queries) == 0

    def test_edit_items_no_per_item_external_resource_queries(self):
        """External resources should be prefetched, not queried per item."""
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                f"/collection/{self.collection.uuid}/edit_items",
            )
        assert response.status_code == 200
        er_individual = [
            q
            for q in ctx.captured_queries
            if "catalog_externalresource" in q["sql"]
            and "IN" not in q["sql"].upper()
            and "item_id" in q["sql"]
        ]
        assert len(er_individual) == 0


@pytest.mark.django_db(databases="__all__")
class TestEditionParentItemTemplateShortCircuit:
    """Test that the template condition avoids Edition.get_work() queries."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="tpl@example.com", username="tpluser")
        self.books = [
            Edition.objects.create(title=f"Template Book {i}") for i in range(3)
        ]
        self.collection = Collection.objects.create(
            title="Template Test Collection",
            owner=self.user.identity,
        )
        for book in self.books:
            self.collection.append_item(book)

    def test_collection_view_no_work_queries(self):
        """Collection view should not query catalog_work for Edition items."""
        client = Client()
        response = client.get(f"/collection/{self.collection.uuid}")
        assert response.status_code == 200
        # Re-request with query capture
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(f"/collection/{self.collection.uuid}")
        assert response.status_code == 200
        work_queries = [
            q
            for q in ctx.captured_queries
            if "catalog_work" in q["sql"] and "catalog_work_editions" in q["sql"]
        ]
        assert len(work_queries) == 0
