import time
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from catalog.models import Edition
from journal.jobs.migrations import backfill_member_progress_from_notes_20260720
from journal.models import (
    Mark,
    Note,
    ShelfMemberProgress,
    ShelfLogEntry,
    ShelfType,
    TagManager,
)
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
def test_set_book_progress_logs_change_without_new_post():
    user = User.register(email="progress@example.com", username="progressuser")
    book = Edition.objects.create(title="Progress Book")
    mark = Mark(user.identity, book)
    mark.update(
        ShelfType.PROGRESS,
        "Reading now",
        7,
        visibility=0,
        metadata={"source": "test"},
    )
    initial_log = mark.logs.get()
    initial_log.metadata["source"] = "test"
    initial_log.save(update_fields=["metadata"])
    initial_post_ids = list(mark.all_post_ids)
    initial_member = mark.shelfmember
    assert initial_member is not None
    initial_member_timestamp = initial_member.created_time

    progress_log = mark.set_progress(Note.ProgressType.PAGE, "42")

    assert progress_log is not None
    assert progress_log.pk != initial_log.pk
    assert progress_log.shelf_type == ShelfType.PROGRESS
    assert progress_log.progress_type == Note.ProgressType.PAGE
    assert progress_log.progress_value == "42"
    assert progress_log.comment_text is None
    assert progress_log.rating_grade is None
    assert progress_log.metadata["source"] == "test"
    assert "comment_text" not in progress_log.metadata
    assert "rating_grade" not in progress_log.metadata
    current_progress = ShelfMemberProgress.objects.get(shelf_member=mark.shelfmember)
    assert current_progress.progress_type == Note.ProgressType.PAGE
    assert current_progress.progress_value == "42"
    updated_member = mark.shelfmember
    assert updated_member is not None
    assert updated_member.created_time == progress_log.timestamp
    assert updated_member.created_time > initial_member_timestamp
    assert list(mark.all_post_ids) == initial_post_ids
    assert mark.progress_display == "Page 42"
    assert mark.progress_short_display == "p42"

    unchanged_log = mark.set_progress(Note.ProgressType.PAGE, "42")
    assert unchanged_log is None
    assert ShelfLogEntry.objects.filter(owner=user.identity, item=book).count() == 2

    cleared_log = mark.set_progress(None, None)
    assert cleared_log is not None
    assert cleared_log.progress_type is None
    assert cleared_log.progress_value is None
    assert cleared_log.comment_text is None
    assert cleared_log.rating_grade is None
    assert mark.progress_display == ""
    assert not ShelfMemberProgress.objects.filter(
        shelf_member=mark.shelfmember
    ).exists()
    assert list(mark.all_post_ids) == initial_post_ids

    mark.set_progress(Note.ProgressType.PAGE, "43")
    assert ShelfMemberProgress.objects.filter(shelf_member=mark.shelfmember).exists()
    mark.update(ShelfType.COMPLETE, visibility=0)
    assert not ShelfMemberProgress.objects.filter(
        shelf_member=mark.shelfmember
    ).exists()

    mark.update(ShelfType.PROGRESS, visibility=0)
    mark.set_progress(Note.ProgressType.PAGE, "44")
    assert ShelfMemberProgress.objects.filter(shelf_member=mark.shelfmember).exists()
    mark.update(ShelfType.DROPPED, visibility=0)
    assert not ShelfMemberProgress.objects.filter(
        shelf_member=mark.shelfmember
    ).exists()


@pytest.mark.django_db(databases="__all__")
def test_set_book_progress_retries_log_timestamp_collision(monkeypatch):
    user = User.register(email="progress-collision@example.com", username="progressdup")
    book = Edition.objects.create(title="Progress Collision Book")
    mark = Mark(user.identity, book)
    mark.update(ShelfType.PROGRESS, visibility=0)
    collision_time = timezone.now() + timedelta(days=1)
    ShelfLogEntry.objects.create(
        owner=user.identity,
        item=book,
        shelf_type=ShelfType.PROGRESS,
        timestamp=collision_time,
    )
    monkeypatch.setattr(timezone, "now", lambda: collision_time)

    progress_log = mark.set_progress(Note.ProgressType.PAGE, "42")

    assert progress_log is not None
    assert progress_log.timestamp == collision_time + timedelta(microseconds=1)
    assert ShelfLogEntry.objects.filter(
        owner=user.identity,
        item=book,
        shelf_type=ShelfType.PROGRESS,
        timestamp=progress_log.timestamp,
        progress_type=Note.ProgressType.PAGE,
        progress_value="42",
    ).exists()


@pytest.mark.django_db(databases="__all__")
def test_backfill_member_progress_from_latest_progress_note():
    user = User.register(email="backfill-progress@example.com", username="backfillprog")
    current_book = Edition.objects.create(title="Backfilled Progress Book")
    dropped_book = Edition.objects.create(title="Dropped Progress Book")
    existing_book = Edition.objects.create(title="Existing Progress Book")
    current_mark = Mark(user.identity, current_book)
    dropped_mark = Mark(user.identity, dropped_book)
    existing_mark = Mark(user.identity, existing_book)
    current_mark.update(ShelfType.PROGRESS, visibility=0)
    dropped_mark.update(ShelfType.PROGRESS, visibility=0)
    existing_mark.update(ShelfType.PROGRESS, visibility=0)

    now = timezone.now()
    Note.objects.create(
        owner=user.identity,
        item=current_book,
        content="Older progress",
        progress_type=Note.ProgressType.PAGE,
        progress_value="12",
        visibility=0,
        created_time=now - timedelta(days=3),
    )
    Note.objects.create(
        owner=user.identity,
        item=current_book,
        content="Latest progress",
        progress_type=Note.ProgressType.CHAPTER,
        progress_value="4",
        visibility=0,
        created_time=now - timedelta(days=2),
    )
    Note.objects.create(
        owner=user.identity,
        item=current_book,
        content="Newer note without progress",
        visibility=0,
        created_time=now - timedelta(days=1),
    )
    Note.objects.create(
        owner=user.identity,
        item=dropped_book,
        content="Dropped progress",
        progress_type=Note.ProgressType.PAGE,
        progress_value="30",
        visibility=0,
        created_time=now,
    )
    Note.objects.create(
        owner=user.identity,
        item=existing_book,
        content="Progress predating an explicit update",
        progress_type=Note.ProgressType.PAGE,
        progress_value="8",
        visibility=0,
        created_time=now,
    )
    existing_mark.set_progress(Note.ProgressType.PAGE, "99")
    dropped_mark.update(ShelfType.DROPPED, visibility=0)

    candidates = backfill_member_progress_from_notes_20260720()

    assert candidates == 1
    progress = ShelfMemberProgress.objects.get(shelf_member=current_mark.shelfmember)
    assert progress.progress_type == Note.ProgressType.CHAPTER
    assert progress.progress_value == "4"
    assert not ShelfMemberProgress.objects.filter(
        shelf_member=dropped_mark.shelfmember
    ).exists()
    assert (
        ShelfMemberProgress.objects.get(
            shelf_member=existing_mark.shelfmember
        ).progress_value
        == "99"
    )


@pytest.mark.django_db(databases="__all__")
def test_current_book_progress_is_not_inferred_from_logs():
    user = User.register(
        email="current-progress@example.com", username="currentprogress"
    )
    book = Edition.objects.create(title="Current Progress Book")
    mark = Mark(user.identity, book)
    mark.update(ShelfType.PROGRESS, visibility=0)
    mark.set_progress(Note.ProgressType.CHAPTER, "3")

    ShelfMemberProgress.objects.filter(shelf_member=mark.shelfmember).delete()

    fresh_mark = Mark(user.identity, book)
    assert fresh_mark.progress_type is None
    assert fresh_mark.progress_value is None
    assert fresh_mark.logs.filter(progress_value="3").exists()


@pytest.mark.django_db(databases="__all__")
def test_edition_and_profile_show_and_update_book_progress(client):
    user = User.register(email="progress-web@example.com", username="progressweb")
    book = Edition.objects.create(title="Progress Web Book")
    Mark(user.identity, book).update(ShelfType.PROGRESS, visibility=0)
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    note_url = reverse("journal:note", args=[book.uuid])

    response = client.get(book.url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "fa-regular fa-square-plus" in content
    assert "fa-solid fa-percent" in content
    assert f"{note_url}?mode=progress" in content

    response = client.post(
        note_url,
        {
            "mode": "progress",
            "progress_type": "chapter",
            "progress_value": "7",
        },
    )
    assert response.status_code == 302
    assert Note.objects.filter(owner=user.identity, item=book).count() == 0

    response = client.get(book.url)
    content = response.content.decode()
    assert "ch7" in content
    assert "Chapter 7" in content

    response = client.get(
        f"/users/{user.username}/profile/book/progress/items",
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert "ch7" in content
    assert 'class="card progress-card"' in content
    assert 'class="progress-badge"' in content
    assert f"{note_url}?mode=progress" in content

    response = client.get(f"{note_url}?mode=progress")
    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="clear_progress"' in content
    assert "fa-regular fa-trash-can" in content

    response = client.post(
        note_url,
        {"mode": "progress", "clear_progress": "1"},
    )
    assert response.status_code == 302
    assert Mark(user.identity, book).progress_value is None

    response = client.get(
        f"/users/{user.username}/profile/book/progress/items",
    )
    assert "fa-solid fa-percent" in response.content.decode()


@pytest.mark.django_db(databases="__all__")
def test_profile_book_progress_badge_hidden_from_non_owner(client):
    owner = User.register(email="progress-owner@example.com", username="progressowner")
    book = Edition.objects.create(title="Private Progress Book")
    mark = Mark(owner.identity, book)
    mark.update(ShelfType.PROGRESS, visibility=0)
    mark.set_progress(Note.ProgressType.CHAPTER, "7")

    viewer = User.register(
        email="progress-viewer@example.com", username="progressviewer"
    )
    client.force_login(viewer, backend="mastodon.auth.OAuth2Backend")

    response = client.get(
        f"/users/{owner.username}/profile/book/progress/items",
    )
    assert response.status_code == 200
    content = response.content.decode()
    # The public shelf still lists the book, but the private reading
    # progress must not leak to anyone other than the owner.
    assert 'class="card progress-card"' in content
    assert book.display_title in content
    assert 'class="progress-badge"' not in content
    assert "ch7" not in content
    assert "Chapter 7" not in content


@pytest.mark.django_db(databases="__all__")
def test_note_and_progress_dialog_modes(client):
    user = User.register(email="note-progress@example.com", username="noteprogress")
    book = Edition.objects.create(title="Note Progress Book")
    mark = Mark(user.identity, book)
    mark.update(ShelfType.PROGRESS, visibility=0)
    mark.set_progress(Note.ProgressType.PAGE, "22")
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    note_url = reverse("journal:note", args=[book.uuid])

    response = client.get(note_url)
    assert response.status_code == 200
    content = response.content.decode()
    assert response.context["mode"] == "note"
    assert response.context["form"]["progress_type"].value() == "page"
    assert response.context["form"]["progress_value"].value() == "22"
    assert response.context["form"]["share_to_mastodon"].value() is False
    assert response.context["form"]["share_to_mastodon"].label == "Crosspost"
    assert 'id="note-mode-selector"' in content
    assert content.index('id="id_update_progress"') < content.index(
        'id="id_share_to_mastodon"'
    )
    assert "Crosspost to timeline" not in content
    assert (
        'data-tooltip="Also publish this note to your connected social accounts."'
        in content
    )

    response = client.get(f"{note_url}?mode=progress")
    assert response.status_code == 200
    assert response.context["mode"] == "progress"
    assert 'id="note-only-fields" hidden' in response.content.decode()

    response = client.post(
        note_url,
        {
            "mode": "progress",
            "progress_type": "track",
            "progress_value": "30",
        },
    )
    assert response.status_code == 400
    assert "progress_type" in response.context["form"].errors
    assert Mark(user.identity, book).progress_value == "22"

    response = client.post(
        note_url,
        {
            "mode": "note",
            "content": "A bound invalid note",
            "visibility": "invalid",
        },
    )
    assert response.status_code == 400
    assert "visibility" in response.context["form"].errors
    assert response.context["form"]["content"].value() == "A bound invalid note"

    response = client.post(
        note_url,
        {
            "mode": "note",
            "content": "A note at another page",
            "title": "",
            "visibility": "0",
            "progress_type": "page",
            "progress_value": "30",
        },
    )
    assert response.status_code == 302
    note = Note.objects.get(owner=user.identity, item=book)
    assert note.progress_value == "30"
    assert Mark(user.identity, book).progress_value == "22"

    response = client.post(
        note_url,
        {
            "mode": "note",
            "content": "A note that updates progress",
            "title": "",
            "visibility": "0",
            "progress_type": "page",
            "progress_value": "31",
            "update_progress": "on",
        },
    )
    assert response.status_code == 302
    assert Mark(user.identity, book).progress_value == "31"

    response = client.get(reverse("journal:note", args=[book.uuid, note.uuid]))
    assert response.status_code == 200
    assert 'id="note-mode-selector"' not in response.content.decode()


@pytest.mark.django_db(databases="__all__")
def test_note_dialog_rejects_progress_update_for_nonprogress_book(client):
    user = User.register(email="note-wishlist@example.com", username="notewishlist")
    book = Edition.objects.create(title="Wishlist Note Book")
    Mark(user.identity, book).update(ShelfType.WISHLIST, visibility=0)
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    note_url = reverse("journal:note", args=[book.uuid])

    response = client.post(
        note_url,
        {
            "mode": "note",
            "content": "Do not update progress",
            "visibility": "0",
            "progress_type": "page",
            "progress_value": "10",
            "update_progress": "on",
        },
    )

    assert response.status_code == 400
    assert "update_progress" in response.context["form"].errors
    assert not Note.objects.filter(owner=user.identity, item=book).exists()


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
