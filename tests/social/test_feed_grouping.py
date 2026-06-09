"""Tests for collapsing consecutive same-author mark posts in the home feed."""

import pytest

from social.feed_grouping import (
    GROUP_THRESHOLD,
    FeedEventGroup,
    group_feed_events,
)

# --- lightweight fakes for the pure-function grouping tests (no DB needed) ---


class _Item:
    def __init__(self, pk: int) -> None:
        self.pk = pk


class _Piece:
    def __init__(self, classname: str) -> None:
        self.classname = classname


class _Post:
    def __init__(
        self, author_id: int, classname: str | None, item_pk: int, comment: bool
    ) -> None:
        self.author = author_id
        self.piece = _Piece(classname) if classname else None
        self.item = _Item(item_pk)
        related: list = [{"type": "Status"}]
        if comment:
            related.append({"type": "Comment"})
        self.type_data = {"object": {"relatedWith": related}}


class _Event:
    def __init__(
        self,
        id: int,
        author_id: int = 1,
        *,
        type: str = "post",
        classname: str | None = "shelfmember",
        item_pk: int | None = None,
        comment: bool = False,
    ) -> None:
        self.id = id
        self.subject_identity_id = author_id
        self.subject_identity = author_id
        self.published = None
        self.type = type
        self.subject_post = _Post(
            author_id, classname, item_pk if item_pk is not None else id, comment
        )


def mark(
    id: int, author_id: int = 1, *, item_pk: int | None = None, comment: bool = False
) -> _Event:
    return _Event(id, author_id, item_pk=item_pk, comment=comment)


def boost(id: int, author_id: int = 1) -> _Event:
    return _Event(id, author_id, type="boost")


def note(id: int, author_id: int = 1) -> _Event:
    return _Event(id, author_id, classname="note")


def review(id: int, author_id: int = 1) -> _Event:
    return _Event(id, author_id, classname="review")


def _types(result: list) -> list:
    """Map a grouping result to a readable shape: 'group(n)' or the event id."""
    out = []
    for r in result:
        if isinstance(r, FeedEventGroup):
            out.append(f"group({len(r.events)})")
        else:
            out.append(r.id)
    return out


class TestGroupFeedEvents:
    def test_run_at_threshold_groups(self):
        events = [mark(30), mark(20), mark(10)]  # newest-first
        result = group_feed_events(events)
        assert len(result) == 1
        group = result[0]
        assert isinstance(group, FeedEventGroup)
        assert group.count == 3
        assert group.is_group is True

    def test_below_threshold_passes_through(self):
        events = [mark(20), mark(10)]
        result = group_feed_events(events)
        assert _types(result) == [20, 10]
        assert not any(isinstance(r, FeedEventGroup) for r in result)

    def test_threshold_constant_is_three(self):
        assert GROUP_THRESHOLD == 3

    def test_pk_is_oldest_event_id(self):
        events = [mark(30), mark(20), mark(10)]
        group = group_feed_events(events)[0]
        assert isinstance(group, FeedEventGroup)
        assert group.pk == 10  # smallest id -> correct ?last= cursor

    def test_author_change_breaks_run(self):
        # two short runs by different authors -> neither reaches threshold
        events = [mark(40, 1), mark(30, 1), mark(20, 2), mark(10, 2)]
        result = group_feed_events(events)
        assert _types(result) == [40, 30, 20, 10]

    def test_two_full_runs_by_different_authors(self):
        events = [
            mark(60, 1),
            mark(50, 1),
            mark(40, 1),
            mark(30, 2),
            mark(20, 2),
            mark(10, 2),
        ]
        result = group_feed_events(events)
        assert _types(result) == ["group(3)", "group(3)"]

    def test_boost_breaks_run(self):
        events = [mark(50), mark(40), boost(30), mark(20), mark(10)]
        # two sub-threshold runs split by a boost -> nothing groups
        result = group_feed_events(events)
        assert _types(result) == [50, 40, 30, 20, 10]

    def test_boost_separates_two_groups(self):
        events = [
            mark(70),
            mark(60),
            mark(50),
            boost(40),
            mark(30),
            mark(20),
            mark(10),
        ]
        result = group_feed_events(events)
        assert _types(result) == ["group(3)", 40, "group(3)"]

    def test_note_and_review_break_runs(self):
        events = [mark(50), mark(40), note(30), review(20), mark(10)]
        result = group_feed_events(events)
        assert _types(result) == [50, 40, 30, 20, 10]

    def test_commented_mark_stays_standalone_and_breaks_run(self):
        events = [
            mark(70),
            mark(60),
            mark(50),
            mark(40, comment=True),
            mark(30),
            mark(20),
            mark(10),
        ]
        result = group_feed_events(events)
        assert _types(result) == ["group(3)", 40, "group(3)"]

    def test_commented_marks_do_not_group(self):
        events = [
            mark(30, comment=True),
            mark(20, comment=True),
            mark(10, comment=True),
        ]
        result = group_feed_events(events)
        assert _types(result) == [30, 20, 10]

    def test_dedupe_items_in_group(self):
        # three events, two reference the same catalog item
        events = [
            mark(30, item_pk=100),
            mark(20, item_pk=100),
            mark(10, item_pk=200),
        ]
        group = group_feed_events(events)[0]
        assert isinstance(group, FeedEventGroup)
        assert len(group.events) == 3
        assert group.count == 2  # distinct items
        assert [it.pk for it in group.items] == [100, 200]

    def test_empty_input(self):
        assert group_feed_events([]) == []

    def test_single_mark_passes_through(self):
        events = [mark(10)]
        result = group_feed_events(events)
        assert _types(result) == [10]


# --- integration test against the real /timeline/data view ---


@pytest.mark.django_db(databases="__all__", transaction=True)
class TestFeedGroupingIntegration:
    def _make_user(self, email: str, username: str):
        from users.models import User

        return User.register(email=email, username=username)

    def test_bulk_comment_less_marks_render_as_one_group(self):
        from django.test import Client

        from catalog.models import Edition
        from journal.models import Mark, ShelfType

        user = self._make_user("grouping@example.com", "grouper")
        books = [Edition.objects.create(title=f"Group Book {i}") for i in range(5)]
        for book in books:
            Mark(user.identity, book).update(ShelfType.WISHLIST, visibility=0)

        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.get("/timeline/data")

        assert response.status_code == 200
        content = response.content.decode()
        # the 5 marks collapse into exactly one group card
        assert content.count('class="activity post mark-group"') == 1
        # all five covers are present in the carousel
        assert content.count("mark-group-covers") == 1
        for book in books:
            assert book.display_title in content

    def test_few_marks_are_not_grouped(self):
        from django.test import Client

        from catalog.models import Edition
        from journal.models import Mark, ShelfType

        user = self._make_user("nogroup@example.com", "ungrouped")
        books = [Edition.objects.create(title=f"Solo Book {i}") for i in range(2)]
        for book in books:
            Mark(user.identity, book).update(ShelfType.WISHLIST, visibility=0)

        client = Client()
        client.force_login(user, backend="mastodon.auth.OAuth2Backend")
        response = client.get("/timeline/data")

        assert response.status_code == 200
        content = response.content.decode()
        assert "mark-group" not in content
