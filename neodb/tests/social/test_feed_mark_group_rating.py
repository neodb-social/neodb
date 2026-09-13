"""Cover cards of a collapsed mark group show the mark author's own rating.

A single item card shows the public average, but a "marked N items" card is one
user's activity, so its covers carry that user's stars, like a profile shelf.
"""

import pytest
from django.test import Client
from django.urls import reverse

from catalog.models import Edition
from common.models import SiteConfig
from journal.models import Mark, Rating, ShelfType
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")

# GROUP_THRESHOLD in social.feed_grouping; fewer marks render as single cards.
MARKS_PER_AUTHOR = 3


@pytest.fixture
def viewer() -> Client:
    user = User.register(email="mgviewer@example.com", username="mgviewer")
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    return client


@pytest.fixture
def world_feed(monkeypatch: pytest.MonkeyPatch) -> str:
    """The world timeline, which shows public posts of users the viewer does
    not follow. It reads the same grouping and rendering path as the home feed.
    """
    opts = SiteConfig.system.model_copy(update={"feed_show_world": True})
    monkeypatch.setattr(SiteConfig, "system", opts)
    monkeypatch.setattr(SiteConfig, "__forced__", True, raising=False)
    return f"{reverse('social:data')}?typ=3"


@pytest.fixture
def items() -> list[Edition]:
    return [
        Edition.objects.create(title=f"MG Book {i}") for i in range(MARKS_PER_AUTHOR)
    ]


def _mark_all(username: str, items: list[Edition], grade: int | None) -> User:
    """Register a user who marks every item, which collapses into one group."""
    user = User.register(email=f"{username}@example.com", username=username)
    for item in items:
        Mark(user.identity, item).update(ShelfType.COMPLETE, None, grade, visibility=0)
    return user


def _groups(content: str) -> list[str]:
    """The mark-group sections of a rendered feed page, in page order."""
    marker = '<section class="activity post mark-group">'
    return [part.split("</section>")[0] for part in content.split(marker)[1:]]


def _group_of(content: str, username: str) -> str:
    sections = [s for s in _groups(content) if f"@{username}" in s]
    assert len(sections) == 1, f"expected one mark group of {username}"
    return sections[0]


def test_each_group_shows_its_own_author_rating(
    viewer: Client, world_feed: str, items: list[Edition]
) -> None:
    """Two authors marking the same items keep their own stars.

    The feed prefetch shares one Item object between both groups, so this is
    the case a per-item rating attribute would get wrong.
    """
    _mark_all("mgalice", items, 8)
    _mark_all("mgbob", items, 4)

    content = viewer.get(world_feed).content.decode()

    alice = _group_of(content, "mgalice")
    assert alice.count('data-rating="8.0"') == MARKS_PER_AUTHOR
    assert 'data-rating="4.0"' not in alice
    bob = _group_of(content, "mgbob")
    assert bob.count('data-rating="4.0"') == MARKS_PER_AUTHOR
    assert 'data-rating="8.0"' not in bob


def test_unrated_marks_show_no_badge(
    viewer: Client, world_feed: str, items: list[Edition]
) -> None:
    """No stars, and no fallback to the public average, when the author did
    not rate the item."""
    for i in range(5):  # journal.models.rating.MIN_RATING_COUNT
        rater = User.register(email=f"mgrater{i}@example.com", username=f"mgrater{i}")
        for item in items:
            Rating.update_item_rating(item, rater.identity, 10)
    for item in items:
        # rating_info is a cached_property, and saving a Rating indexes the
        # item, so read the average from a freshly loaded row
        assert Edition.objects.get(pk=item.pk).rating, (
            "the items need a public average for this test to bite"
        )
    _mark_all("mgcarol", items, None)

    group = _group_of(viewer.get(world_feed).content.decode(), "mgcarol")

    assert "dc-badge" not in group


def test_rating_hidden_from_viewer_who_may_not_see_it(
    viewer: Client, world_feed: str, items: list[Edition]
) -> None:
    """A public mark whose rating is followers-only shows no stars to a
    stranger, the same as on a profile shelf."""
    author = _mark_all("mgdave", items, 6)
    Rating.objects.filter(owner=author.identity).update(visibility=1)

    group = _group_of(viewer.get(world_feed).content.decode(), "mgdave")

    assert "dc-card" in group  # the covers still render
    assert "data-rating" not in group
