"""Tests for N+1 query optimizations."""

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from catalog.models import (
    CreditRole,
    Edition,
    ExternalResource,
    IdType,
    ItemCredit,
    Movie,
    People,
    PeopleRole,
    PeopleType,
    Work,
)
from catalog.models.people import ItemPeopleRelation
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


@pytest.mark.django_db(databases="__all__")
class TestMarkLatestPostPrefetch:
    """Test that Mark.get_marks_by_items prefetches latest_post for pieces."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.users = [
            User.register(email=f"mlp{i}@example.com", username=f"mlpuser{i}")
            for i in range(3)
        ]
        self.book = Edition.objects.create(title="Latest Post Book")
        for i, user in enumerate(self.users):
            Mark(user.identity, self.book).update(
                ShelfType.COMPLETE, f"comment {i}", i + 5, [], 0
            )

    def test_marks_have_prefetched_latest_post(self):
        """After get_marks_by_items, latest_post should be in __dict__ (prefetched)."""
        marks = Mark.get_marks_by_items(
            self.users[0].identity, [self.book], self.users[0]
        )
        m = marks[self.book.pk]
        # shelfmember and comment should have latest_post pre-set in __dict__
        if m.shelfmember:
            assert "latest_post_id" in m.shelfmember.__dict__
            assert "latest_post" in m.shelfmember.__dict__
        if m.comment:
            assert "latest_post_id" in m.comment.__dict__
            assert "latest_post" in m.comment.__dict__

    def test_no_per_piece_post_queries(self):
        """After prefetch, accessing latest_post should not trigger queries."""
        marks = Mark.get_marks_by_items(
            self.users[0].identity, [self.book], self.users[0]
        )
        m = marks[self.book.pk]
        with CaptureQueriesContext(connection) as ctx:
            if m.shelfmember:
                _ = m.shelfmember.latest_post
            if m.comment:
                _ = m.comment.latest_post
            if m.review:
                _ = m.review.latest_post
        assert len(ctx.captured_queries) == 0


@pytest.mark.django_db(databases="__all__")
class TestTagListLatestPostPrefetch:
    """Test that tag/shelf list pages do not have N+1 on latest_post."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="tlp@example.com", username="tlpuser")
        self.books = [Edition.objects.create(title=f"TLP Book {i}") for i in range(5)]
        self.tag = Tag.objects.create(
            owner=self.user.identity, title="tlp-tag", visibility=0
        )
        for book in self.books:
            Mark(self.user.identity, book).update(
                ShelfType.COMPLETE, f"comment for {book.title}", 7, ["tlp-tag"], 0
            )
            self.tag.append_item(book)

    def test_tag_list_no_per_item_piecepost_queries(self):
        """Tag list should batch PiecePost queries, not query per item."""
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                f"/users/{self.user.username}/tags/tlp-tag/",
            )
        assert response.status_code == 200
        # Individual PiecePost queries (not using IN) indicate N+1
        piecepost_individual = [
            q
            for q in ctx.captured_queries
            if "journal_piecepost" in q["sql"]
            and "piece_id" in q["sql"]
            and "IN" not in q["sql"].upper()
        ]
        assert len(piecepost_individual) == 0

    def test_tag_list_no_per_item_post_queries(self):
        """Tag list should not query activities_post individually per item."""
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                f"/users/{self.user.username}/tags/tlp-tag/",
            )
        assert response.status_code == 200
        post_individual = [
            q
            for q in ctx.captured_queries
            if "activities_post" in q["sql"]
            and 'WHERE "activities_post"."id"' in q["sql"]
        ]
        assert len(post_individual) == 0

    def test_shelf_list_no_per_item_piecepost_queries(self):
        """Shelf list should batch PiecePost queries."""
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                f"/users/{self.user.username}/complete/book/",
            )
        assert response.status_code == 200
        piecepost_individual = [
            q
            for q in ctx.captured_queries
            if "journal_piecepost" in q["sql"]
            and "piece_id" in q["sql"]
            and "IN" not in q["sql"].upper()
        ]
        assert len(piecepost_individual) == 0


@pytest.mark.django_db(databases="__all__")
class TestCollectionListLatestPostPrefetch:
    """Test that collection list page prefetches latest_post."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="cllp@example.com", username="cllpuser")
        self.collections = []
        for i in range(3):
            c = Collection.objects.create(
                title=f"CL Collection {i}",
                owner=self.user.identity,
            )
            book = Edition.objects.create(title=f"CL Book {i}")
            c.append_item(book)
            self.collections.append(c)

    def test_collection_list_no_per_collection_piecepost_queries(self):
        """Collection list should batch PiecePost queries."""
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                f"/users/{self.user.username}/collections/",
            )
        assert response.status_code == 200
        piecepost_individual = [
            q
            for q in ctx.captured_queries
            if "journal_piecepost" in q["sql"]
            and "piece_id" in q["sql"]
            and "IN" not in q["sql"].upper()
        ]
        assert len(piecepost_individual) == 0


@pytest.mark.django_db(databases="__all__")
class TestCollectionGetStats:
    """Test that Collection.get_stats() uses a single aggregation query."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="cstats@example.com", username="cstatsuser")
        self.books = [Edition.objects.create(title=f"Stats Book {i}") for i in range(5)]
        self.collection = Collection.objects.create(
            title="Stats Collection",
            owner=self.user.identity,
        )
        for book in self.books:
            self.collection.append_item(book)
        # Mark some items on different shelves
        Mark(self.user.identity, self.books[0]).update(ShelfType.WISHLIST, visibility=0)
        Mark(self.user.identity, self.books[1]).update(ShelfType.COMPLETE, visibility=0)
        Mark(self.user.identity, self.books[2]).update(ShelfType.COMPLETE, visibility=0)

    def test_get_stats_returns_correct_counts(self):
        stats = self.collection.get_stats(self.user.identity)
        assert stats["total"] == 5
        assert stats["wishlist"] == 1
        assert stats["complete"] == 2
        assert stats["progress"] == 0
        assert stats["dropped"] == 0
        assert stats["percentage"] == round(2 * 100 / 5)

    def test_get_stats_bounded_queries(self):
        """get_stats should use a single aggregation query, not one per shelf type."""
        # Warm up shelf_manager
        _ = self.user.identity.shelf_manager
        with CaptureQueriesContext(connection) as ctx:
            self.collection.get_stats(self.user.identity)
        shelfmember_queries = [
            q for q in ctx.captured_queries if "journal_shelfmember" in q["sql"]
        ]
        # Should be 1 aggregation query, not 5 (one per shelf type)
        assert len(shelfmember_queries) == 1


@pytest.mark.django_db(databases="__all__")
class TestProfileShelfItemsPrefetch:
    """Test that profile_shelf_items view prefetches items and rating info."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="psi@example.com", username="psiuser")
        self.books = [Edition.objects.create(title=f"PSI Book {i}") for i in range(3)]
        for i, book in enumerate(self.books):
            ExternalResource.objects.create(
                item=book,
                id_type=IdType.ISBN,
                id_value=f"978222200000{book.pk}",
                url=f"https://example.com/psi/{book.pk}",
            )
            Mark(self.user.identity, book).update(
                ShelfType.WISHLIST, f"note {i}", i + 5, [], 0
            )

    def test_shelf_items_renders(self):
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        response = client.get(
            f"/users/{self.user.username}/profile/book/wishlist/items",
        )
        assert response.status_code == 200
        for book in self.books:
            assert book.display_title in response.content.decode()

    def test_shelf_items_no_per_item_queries(self):
        """Profile shelf items should batch-fetch items, not query per member."""
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                f"/users/{self.user.username}/profile/book/wishlist/items",
            )
        assert response.status_code == 200
        individual_item_queries = [
            q
            for q in ctx.captured_queries
            if "catalog_item" in q["sql"] and 'WHERE "catalog_item"."id" =' in q["sql"]
        ]
        assert len(individual_item_queries) == 0

    def test_shelf_items_no_per_item_rating_queries(self):
        """Rating info should be batch-fetched, not queried per item."""
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                f"/users/{self.user.username}/profile/book/wishlist/items",
            )
        assert response.status_code == 200
        individual_rating_queries = [
            q
            for q in ctx.captured_queries
            if "journal_rating" in q["sql"]
            and "GROUP BY" in q["sql"]
            and "IN" not in q["sql"].upper()
        ]
        assert len(individual_rating_queries) == 0


@pytest.mark.django_db(databases="__all__")
class TestRenderListRatingPrefetch:
    """Test that render_list batch-prefetches rating info for items."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="rlr@example.com", username="rlruser")
        self.books = [Edition.objects.create(title=f"RLR Book {i}") for i in range(3)]
        self.tag = Tag.objects.create(
            owner=self.user.identity, title="rlr-tag", visibility=0
        )
        for book in self.books:
            Mark(self.user.identity, book).update(
                ShelfType.WISHLIST, f"comment {book.title}", 7, ["rlr-tag"], 0
            )
            self.tag.append_item(book)

    def test_tag_list_no_per_item_rating_queries(self):
        """Tag list should batch-fetch rating info, not query per item."""
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                f"/users/{self.user.username}/tags/rlr-tag/",
            )
        assert response.status_code == 200
        individual_rating_queries = [
            q
            for q in ctx.captured_queries
            if "journal_rating" in q["sql"]
            and "GROUP BY" in q["sql"]
            and "IN" not in q["sql"].upper()
        ]
        assert len(individual_rating_queries) == 0

    def test_shelf_list_no_per_item_rating_queries(self):
        """Shelf list should batch-fetch rating info, not query per item."""
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                f"/users/{self.user.username}/wishlist/book/",
            )
        assert response.status_code == 200
        individual_rating_queries = [
            q
            for q in ctx.captured_queries
            if "journal_rating" in q["sql"]
            and "GROUP BY" in q["sql"]
            and "IN" not in q["sql"].upper()
        ]
        assert len(individual_rating_queries) == 0


@pytest.mark.django_db(databases="__all__")
class TestMarkBatchFetchNoFKResolution:
    """Test that Mark.get_marks_by_items uses item_id, not item.pk."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="fk@example.com", username="fkuser")
        self.books = [Edition.objects.create(title=f"FK Book {i}") for i in range(3)]
        for book in self.books:
            Mark(self.user.identity, book).update(
                ShelfType.COMPLETE, "comment", 8, ["tag"], 0
            )

    def test_no_individual_item_queries(self):
        """get_marks_by_items should not trigger per-item catalog_item queries."""
        with CaptureQueriesContext(connection) as ctx:
            Mark.get_marks_by_items(self.user.identity, self.books, self.user)
        individual_item_queries = [
            q
            for q in ctx.captured_queries
            if "catalog_item" in q["sql"] and 'WHERE "catalog_item"."id" =' in q["sql"]
        ]
        assert len(individual_item_queries) == 0


def _per_item_credit_queries(captured_queries):
    return [
        q
        for q in captured_queries
        if "catalog_itemcredit" in q["sql"]
        and '"catalog_itemcredit"."item_id" =' in q["sql"]
    ]


def _per_item_external_resource_queries(captured_queries):
    return [
        q
        for q in captured_queries
        if "catalog_externalresource" in q["sql"]
        and '"catalog_externalresource"."item_id" =' in q["sql"]
    ]


def _per_item_rating_group_queries(captured_queries):
    return [
        q
        for q in captured_queries
        if "journal_rating" in q["sql"]
        and "GROUP BY" in q["sql"]
        and '"journal_rating"."item_id" =' in q["sql"]
    ]


@pytest.mark.django_db(databases="__all__")
class TestPeopleWorksPrefetch:
    """people_works view should batch-fetch credits, external_resources, and ratings."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="pw@example.com", username="pwuser")
        self.author = People.objects.create(
            title="PW Author",
            people_type=PeopleType.PERSON,
            metadata={"localized_name": [{"lang": "en", "text": "PW Author"}]},
        )
        self.books = []
        for i in range(4):
            book = Edition.objects.create(title=f"PW Book {i}")
            ItemCredit.objects.create(
                item=book,
                person=self.author,
                role=CreditRole.Author,
                name=self.author.display_name,
            )
            ItemPeopleRelation.objects.create(
                item=book, people=self.author, role=PeopleRole.AUTHOR
            )
            ExternalResource.objects.create(
                item=book,
                id_type=IdType.ISBN,
                id_value=f"978222200000{i}",
                url=f"https://example.com/pwbook/{i}",
            )
            self.books.append(book)

    def _fetch(self):
        client = Client()
        url = f"/people/{self.author.uuid}/works/{PeopleRole.AUTHOR.value}"
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(url)
        return response, ctx

    def test_page_renders(self):
        response, _ = self._fetch()
        assert response.status_code == 200
        body = response.content.decode()
        for book in self.books:
            assert book.display_title in body

    def _queries_for_book_ids(self, queries):
        """Keep only queries whose item_id = <book_pk> for one of the listed works.

        The People sidebar item triggers its own single-item lookups that are
        not N+1, so we scope assertions to the per-work page items.
        """
        book_ids = {b.pk for b in self.books}
        return [
            q for q in queries if any(f'item_id" = {pk}' in q["sql"] for pk in book_ids)
        ]

    def test_no_per_item_credits_queries(self):
        """Credits should be prefetched, not queried per work."""
        response, ctx = self._fetch()
        assert response.status_code == 200
        assert (
            self._queries_for_book_ids(_per_item_credit_queries(ctx.captured_queries))
            == []
        )

    def test_no_per_item_external_resource_queries(self):
        response, ctx = self._fetch()
        assert response.status_code == 200
        assert (
            self._queries_for_book_ids(
                _per_item_external_resource_queries(ctx.captured_queries)
            )
            == []
        )

    def test_no_per_item_rating_queries(self):
        response, ctx = self._fetch()
        assert response.status_code == 200
        assert (
            self._queries_for_book_ids(
                _per_item_rating_group_queries(ctx.captured_queries)
            )
            == []
        )


@pytest.mark.django_db(databases="__all__")
class TestItemRetrieveCreditsPrefetch:
    """catalog.retrieve (edition detail page) should only query credits once."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="ir@example.com", username="iruser")
        self.author = People.objects.create(
            title="IR Author",
            people_type=PeopleType.PERSON,
            metadata={"localized_name": [{"lang": "en", "text": "IR Author"}]},
        )
        self.translator = People.objects.create(
            title="IR Translator",
            people_type=PeopleType.PERSON,
            metadata={"localized_name": [{"lang": "en", "text": "IR Translator"}]},
        )
        self.book = Edition.objects.create(title="IR Book")
        ItemCredit.objects.create(
            item=self.book,
            person=self.author,
            role=CreditRole.Author,
            name=self.author.display_name,
        )
        ItemCredit.objects.create(
            item=self.book,
            person=self.translator,
            role=CreditRole.Translator,
            name=self.translator.display_name,
        )

    def test_credits_queried_at_most_once(self):
        """edition.html reads role_credits multiple times; prefetch means 1 DB hit."""
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(self.book.url)
        assert response.status_code == 200
        credit_queries = [
            q
            for q in ctx.captured_queries
            if "catalog_itemcredit" in q["sql"] and "SELECT" in q["sql"].upper()
        ]
        # One prefetch query is allowed; should never scale with access count.
        assert len(credit_queries) <= 1

    def test_sibling_editions_credits_prefetched(self):
        """edition.html shows publisher_name per sibling edition; must not N+1."""
        work = Work.objects.create(title="IR Work")
        work.editions.add(self.book)
        publisher = People.objects.create(
            title="IR Publisher",
            people_type=PeopleType.ORGANIZATION,
            metadata={"localized_name": [{"lang": "en", "text": "IR Publisher"}]},
        )
        for i in range(3):
            sibling = Edition.objects.create(title=f"IR Sibling {i}")
            work.editions.add(sibling)
            ItemCredit.objects.create(
                item=sibling,
                person=publisher,
                role=CreditRole.Publisher,
                name=publisher.display_name,
            )
        client = Client()
        client.force_login(self.user, backend="mastodon.auth.OAuth2Backend")
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(self.book.url)
        assert response.status_code == 200
        credit_queries = [
            q
            for q in ctx.captured_queries
            if "catalog_itemcredit" in q["sql"] and "SELECT" in q["sql"].upper()
        ]
        # One for the main item + one prefetch batch for siblings = at most 2.
        assert len(credit_queries) <= 2


@pytest.mark.django_db(databases="__all__")
class TestShelfAPICreditsPrefetch:
    """Shelf API must prefetch item credits across paginated marks."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="sac@example.com", username="sacuser")
        self.author = People.objects.create(
            title="SAC Author",
            people_type=PeopleType.PERSON,
            metadata={"localized_name": [{"lang": "en", "text": "SAC Author"}]},
        )
        self.books = []
        for i in range(3):
            book = Edition.objects.create(title=f"SAC Book {i}")
            ItemCredit.objects.create(
                item=book,
                person=self.author,
                role=CreditRole.Author,
                name=self.author.display_name,
            )
            Mark(self.user.identity, book).update(
                ShelfType.WISHLIST, f"n{i}", i + 5, [], 0
            )
            self.books.append(book)
        self.app = Takahe.get_or_create_app(
            "Test",
            "https://example.org",
            "https://example.org/cb",
            owner_pk=self.user.identity.pk,
        )
        self.token = Takahe.refresh_token(self.app, self.user.identity.pk, self.user.pk)

    def test_shelf_api_no_per_item_credit_queries(self):
        client = Client()
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                "/api/me/shelf/wishlist",
                HTTP_AUTHORIZATION=f"Bearer {self.token}",
            )
        assert response.status_code == 200
        assert _per_item_credit_queries(ctx.captured_queries) == []


@pytest.mark.django_db(databases="__all__")
class TestShelfAPIEditionWorksPrefetch:
    """Shelf API must prefetch Edition.works so ItemSchema.parent_uuid doesn't N+1."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="saw@example.com", username="sawuser")
        self.work = Work.objects.create(title="Shelf Work")
        self.editions = []
        for i in range(4):
            e = Edition.objects.create(title=f"SAW Edition {i}")
            self.work.editions.add(e)
            Mark(self.user.identity, e).update(
                ShelfType.WISHLIST, f"n{i}", i + 5, [], 0
            )
            self.editions.append(e)
        self.app = Takahe.get_or_create_app(
            "Test",
            "https://example.org",
            "https://example.org/cb",
            owner_pk=self.user.identity.pk,
        )
        self.token = Takahe.refresh_token(self.app, self.user.identity.pk, self.user.pk)

    def test_shelf_api_no_per_edition_work_queries(self):
        client = Client()
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(
                "/api/me/shelf/wishlist",
                HTTP_AUTHORIZATION=f"Bearer {self.token}",
            )
        assert response.status_code == 200
        work_queries = [
            q
            for q in ctx.captured_queries
            if "catalog_work_editions" in q["sql"]
            and '"catalog_work_editions"."edition_id" =' in q["sql"]
        ]
        assert work_queries == []


@pytest.mark.django_db(databases="__all__")
class TestReviewsPrefetch:
    """/item/.../reviews should batch rating, latest_post, and identity lookups."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.book = Edition.objects.create(title="Review Book")
        self.reviewers = [
            User.register(email=f"rv{i}@example.com", username=f"rvuser{i}")
            for i in range(3)
        ]
        for i, u in enumerate(self.reviewers):
            Mark(u.identity, self.book).update(ShelfType.COMPLETE, None, i + 5, [], 0)
            u.identity.shelf_manager  # ensure manager exists
            from journal.models import Review

            Review.objects.create(
                owner=u.identity,
                item=self.book,
                title=f"Title {i}",
                body=f"Body {i}",
            )

    def test_reviews_page_renders(self):
        client = Client()
        response = client.get(f"/book/{self.book.uuid}/reviews")
        assert response.status_code == 200

    def test_reviews_no_per_review_rating_queries(self):
        client = Client()
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(f"/book/{self.book.uuid}/reviews")
        assert response.status_code == 200
        # Per-review rating_grade lookups would match this pattern
        rating_queries = [
            q
            for q in ctx.captured_queries
            if "journal_rating" in q["sql"]
            and '"journal_rating"."owner_id" =' in q["sql"]
            and '"journal_rating"."item_id" =' in q["sql"]
            and "IN" not in q["sql"].upper()
        ]
        assert rating_queries == []

    def test_reviews_no_per_review_identity_queries(self):
        client = Client()
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(f"/book/{self.book.uuid}/reviews")
        assert response.status_code == 200
        identity_queries = [
            q
            for q in ctx.captured_queries
            if "users_identity" in q["sql"]
            and 'WHERE "users_identity"."id" =' in q["sql"]
        ]
        assert identity_queries == []


@pytest.mark.django_db(databases="__all__")
class TestCollectionMemberParent:
    """Collection page must not dereference member.parent per collection member."""

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.user = User.register(email="cmp@example.com", username="cmpuser")
        self.collection = Collection.objects.create(
            title="CMP Collection", owner=self.user.identity
        )
        for i in range(5):
            self.collection.append_item(Edition.objects.create(title=f"CMP Book {i}"))

    def test_no_per_member_collection_polymorphic_queries(self):
        client = Client()
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(f"/collection/{self.collection.uuid}")
        assert response.status_code == 200
        # A per-member parent dereference would polymorphically fetch
        # journal_collection joined with journal_piece by piece_ptr_id.
        parent_queries = [
            q
            for q in ctx.captured_queries
            if "journal_collection" in q["sql"]
            and "journal_piece" in q["sql"]
            and '"journal_collection"."piece_ptr_id" =' in q["sql"]
        ]
        # One top-level collection fetch is expected; members should not add more.
        assert len(parent_queries) <= 1
