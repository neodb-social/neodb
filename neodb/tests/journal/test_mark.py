import time

import pytest
from django.core.cache import cache
from django.urls import reverse

from catalog.models import Edition
from journal.models import Mark, ShelfType, TagManager
from takahe.utils import Takahe
from users.models import User


@pytest.mark.django_db(databases="__all__")
def test_tag_manager_recent_orders_by_last_used():
    user = User.register(email="recent@example.com", username="recentuser")
    other = User.register(email="other-recent@example.com", username="otherrecent")
    book_a = Edition.objects.create(title="A")
    book_b = Edition.objects.create(title="B")

    TagManager.tag_item_for_owner(user.identity, book_a, ["alpha"])
    time.sleep(0.01)
    TagManager.tag_item_for_owner(user.identity, book_b, ["beta"])
    TagManager.tag_item_for_owner(other.identity, book_a, ["other-only"])

    titles = user.identity.tag_manager.get_recent_titles(limit=10)
    assert titles == ["beta", "alpha"]


@pytest.mark.django_db(databases="__all__")
def test_tag_manager_popular_orders_by_count():
    user = User.register(email="popular@example.com", username="popularuser")
    other = User.register(email="other-pop@example.com", username="otherpop")
    b1 = Edition.objects.create(title="b1")
    b2 = Edition.objects.create(title="b2")
    b3 = Edition.objects.create(title="b3")

    TagManager.tag_item_for_owner(user.identity, b1, ["common", "rare"])
    TagManager.tag_item_for_owner(user.identity, b2, ["common"])
    TagManager.tag_item_for_owner(user.identity, b3, ["common"])
    TagManager.tag_item_for_owner(other.identity, b1, ["other-only"])

    titles = user.identity.tag_manager.get_popular_titles(limit=10)
    assert titles == ["common", "rare"]


@pytest.mark.django_db(databases="__all__")
def test_tag_manager_popular_caches_for_24h():
    user = User.register(email="cache@example.com", username="cacheuser")
    book = Edition.objects.create(title="cached")
    cache.delete(f"tag_pop:{user.identity.pk}")

    TagManager.tag_item_for_owner(user.identity, book, ["seen"])
    first = user.identity.tag_manager.get_cached_popular_titles()
    assert first == ["seen"]

    # New tag added after the cache is populated should NOT show up until TTL.
    book2 = Edition.objects.create(title="cached2")
    TagManager.tag_item_for_owner(user.identity, book2, ["unseen"])
    second = user.identity.tag_manager.get_cached_popular_titles()
    assert second == ["seen"]

    cache.delete(f"tag_pop:{user.identity.pk}")
    third = user.identity.tag_manager.get_cached_popular_titles()
    assert set(third) == {"seen", "unseen"}


@pytest.mark.django_db(databases="__all__")
def test_mark_editor_embeds_recent_and_popular_tags(client):
    user = User.register(email="mark-tags@example.com", username="marktags")
    other = User.register(email="other-mark@example.com", username="othermark")
    book = Edition.objects.create(title="Marked Book")
    other_book = Edition.objects.create(title="Other Marked Book")

    TagManager.tag_item_for_owner(user.identity, book, ["current"])
    TagManager.tag_item_for_owner(user.identity, other_book, ["future"])
    TagManager.tag_item_for_owner(other.identity, book, ["other-only"])
    cache.delete(f"tag_pop:{user.identity.pk}")

    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    response = client.get(reverse("journal:mark", args=[book.uuid]))

    assert response.status_code == 200
    assert response.context["tags"] == ["current"]
    assert "future" in response.context["recent_tags"]
    assert "current" in response.context["recent_tags"]
    assert set(response.context["popular_tags"]) == {"current", "future"}
    assert "other-only" not in response.content.decode()


@pytest.mark.django_db(databases="__all__")
def test_tag_suggestions_endpoint_is_gone(client):
    user = User.register(email="gone@example.com", username="goneuser")
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    with pytest.raises(Exception):
        reverse("journal:tag_suggestions")


@pytest.mark.django_db(databases="__all__")
class TestMarkWithPosts:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.book = Edition.objects.create(title="Test Book")
        self.user = User.register(email="test@example.com", username="testuser")

    def test_mark_book_progression_with_posts(self):
        """Test marking a book through different statuses and verify posts are created correctly"""
        mark = Mark(self.user.identity, self.book)

        # Initial state - no posts
        assert len(mark.all_post_ids) == 0
        assert len(mark.current_post_ids) == 0
        assert mark.latest_post_id is None
        assert len(mark.logs) == 0

        # Step 1: Mark as wishlist with comment
        mark.update(ShelfType.WISHLIST, "Want to read this book", visibility=1)

        # Refresh mark to get updated data
        mark = Mark(self.user.identity, self.book)

        # Verify wishlist state
        assert mark.shelf_type == ShelfType.WISHLIST
        assert mark.comment_text == "Want to read this book"
        assert mark.visibility == 1

        # Verify posts and logs
        wishlist_logs = list(mark.logs)
        assert len(wishlist_logs) == 1
        assert wishlist_logs[0].shelf_type == "wishlist"

        wishlist_all_posts = list(mark.all_post_ids)
        wishlist_current_posts = list(mark.current_post_ids)
        wishlist_latest_post_id = mark.latest_post_id

        assert len(wishlist_all_posts) == 1
        assert len(wishlist_current_posts) == 1
        assert wishlist_latest_post_id is not None
        assert Takahe.get_post(wishlist_latest_post_id) is not None
        assert (
            wishlist_all_posts[0]
            == wishlist_current_posts[0]
            == wishlist_latest_post_id
        )

        # Step 2: Mark as reading with different comment
        time.sleep(0.001)
        mark.update(
            ShelfType.PROGRESS, "Started reading, looks interesting", visibility=1
        )

        # Refresh mark to get updated data
        mark = Mark(self.user.identity, self.book)

        # Verify reading state
        assert mark.shelf_type == ShelfType.PROGRESS
        assert mark.comment_text == "Started reading, looks interesting"
        assert mark.visibility == 1

        # Verify posts and logs
        logs = list(mark.logs)
        assert len(logs) == 2
        assert [
            (log.shelf_type, log.comment_text, log.rating_grade) for log in logs
        ] == [
            ("wishlist", "Want to read this book", None),
            ("progress", "Started reading, looks interesting", None),
        ]
        reading_all_posts = list(mark.all_post_ids)
        reading_current_posts = list(mark.current_post_ids)
        reading_latest_post_id = mark.latest_post_id

        assert len(reading_all_posts) == 2
        assert len(reading_current_posts) == 1
        assert reading_latest_post_id is not None
        assert Takahe.get_post(reading_latest_post_id) is not None

        # Verify the new post is different from wishlist post
        assert reading_latest_post_id != wishlist_latest_post_id
        assert reading_latest_post_id in reading_all_posts
        assert wishlist_latest_post_id in reading_all_posts
        assert reading_latest_post_id == reading_current_posts[0]

        # Step 3: Mark as completed with final comment
        time.sleep(0.001)
        mark.update(
            ShelfType.COMPLETE, "Finished reading, excellent book!", 8, visibility=1
        )

        # Refresh mark to get updated data
        mark = Mark(self.user.identity, self.book)

        # Verify completed state
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.comment_text == "Finished reading, excellent book!"
        assert mark.visibility == 1

        # Verify final posts and logs
        logs = list(mark.logs)
        assert len(logs) == 3
        assert [
            (log.shelf_type, log.comment_text, log.rating_grade) for log in logs
        ] == [
            ("wishlist", "Want to read this book", None),
            ("progress", "Started reading, looks interesting", None),
            ("complete", "Finished reading, excellent book!", 8),
        ]

        final_all_posts = list(mark.all_post_ids)
        final_current_posts = list(mark.current_post_ids)
        final_latest_post_id = mark.latest_post_id

        # Should have 3 posts total (one for each status change)
        assert len(final_all_posts) == 3
        assert len(final_current_posts) == 1
        assert final_latest_post_id is not None
        final_latest_post = Takahe.get_post(final_latest_post_id)
        assert final_latest_post is not None
        assert final_latest_post.state == "new"

        # Verify the latest post is different from previous posts
        assert final_latest_post_id != reading_latest_post_id
        assert final_latest_post_id != wishlist_latest_post_id
        assert final_latest_post_id == final_current_posts[0]

        # Verify all posts are unique
        assert len(set(final_all_posts)) == 3

        # Verify post order and content
        assert wishlist_latest_post_id in final_all_posts
        assert reading_latest_post_id in final_all_posts
        assert final_latest_post_id in final_all_posts

        # previous posts should still exists (not deleted)
        wishlist_latest_post = Takahe.get_post(wishlist_latest_post_id)
        assert wishlist_latest_post is not None
        assert wishlist_latest_post.state == "new"
        reading_latest_post = Takahe.get_post(reading_latest_post_id)
        assert reading_latest_post is not None
        assert reading_latest_post.state == "new"

        # Step 4: Change mark visibility
        time.sleep(0.001)
        mark.update(
            ShelfType.COMPLETE, "Finished reading, excellent book!", visibility=2
        )
        mark = Mark(self.user.identity, self.book)

        # Verify completed state
        assert mark.shelf_type == ShelfType.COMPLETE
        assert mark.comment_text == "Finished reading, excellent book!"
        assert mark.visibility == 2

        # Verify final posts and logs
        logs = list(mark.logs)
        assert len(logs) == 3
        assert [
            (log.shelf_type, log.comment_text, log.rating_grade) for log in logs
        ] == [
            ("wishlist", "Want to read this book", None),
            ("progress", "Started reading, looks interesting", None),
            ("complete", "Finished reading, excellent book!", 8),
        ]

        final2_all_posts = list(mark.all_post_ids)
        final2_current_posts = list(mark.current_post_ids)
        final2_latest_post_id = mark.latest_post_id

        # Should have 3 posts total (one for each status change)
        assert len(final2_all_posts) == 4
        assert len(final2_current_posts) == 2
        assert final_latest_post_id is not None
        assert final2_latest_post_id is not None
        final2_latest_post = Takahe.get_post(final2_latest_post_id)
        assert final2_latest_post is not None
        assert final2_latest_post.state == "new"

        # Verify the latest post is different from previous posts
        assert final2_latest_post != reading_latest_post_id
        assert final2_latest_post != wishlist_latest_post_id
        assert final2_latest_post != final_latest_post
        assert final2_latest_post_id in final2_current_posts
        assert final_latest_post_id in final2_current_posts

        assert len(set(final2_all_posts)) == 4
        assert wishlist_latest_post_id in final2_all_posts
        assert reading_latest_post_id in final2_all_posts
        assert final_latest_post_id in final2_all_posts
        assert final2_latest_post_id in final2_all_posts

        # verify the previous complete post was deleted
        final_latest_post = Takahe.get_post(final_latest_post_id)
        assert final_latest_post is not None
        assert final_latest_post.state == "deleted"

        # Step 5: back to reading for the 2nd time
        time.sleep(0.001)
        mark.update(ShelfType.PROGRESS, "Started reading again", visibility=2)

        # Refresh mark to get updated data
        mark = Mark(self.user.identity, self.book)

        # Verify reading state
        assert mark.shelf_type == ShelfType.PROGRESS
        assert mark.comment_text == "Started reading again"
        assert mark.rating_grade == 8  # this is current behavior
        assert mark.visibility == 2

        # Verify posts and logs
        logs = list(mark.logs)
        assert len(logs) == 4
        assert [
            (log.shelf_type, log.comment_text, log.rating_grade) for log in logs
        ] == [
            ("wishlist", "Want to read this book", None),
            ("progress", "Started reading, looks interesting", None),
            ("complete", "Finished reading, excellent book!", 8),
            ("progress", "Started reading again", 8),
        ]

        reading_all_posts = list(mark.all_post_ids)
        reading_current_posts = list(mark.current_post_ids)
        reading2_latest_post_id = mark.latest_post_id

        assert len(reading_all_posts) == 5
        assert len(reading_current_posts) == 1
        assert reading2_latest_post_id is not None
        assert reading2_latest_post_id in reading_all_posts
        assert reading2_latest_post_id == reading_current_posts[0]
        assert reading2_latest_post_id != reading_latest_post_id

        reading2_latest_post = Takahe.get_post(reading2_latest_post_id)
        assert reading2_latest_post is not None
        assert reading2_latest_post.state == "new"

        # verify the previous reading post was not deleted
        reading_latest_post = Takahe.get_post(reading_latest_post_id)
        assert reading_latest_post is not None
        assert reading_latest_post.state == "new"
