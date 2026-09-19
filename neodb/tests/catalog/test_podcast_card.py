"""The podcast item card offers the show's newest episode for in-page play."""

import re
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from catalog.models import Edition, Item, Podcast, PodcastEpisode
from journal.models import Collection
from users.models import User

pytestmark = pytest.mark.django_db(databases="__all__")


def _show(title: str, *episodes: tuple[str, int]) -> Podcast:
    """A podcast with ``(episode title, days ago)`` episodes.

    The episodes are created oldest-last so that a test cannot pass on
    insertion order alone.
    """
    show = Podcast.objects.create(title=title, host=["A Host"])
    show.sync_credits_from_metadata()
    now = timezone.now()
    for episode_title, days_ago in episodes:
        PodcastEpisode.objects.create(
            title=episode_title,
            program=show,
            pub_date=now - timedelta(days=days_ago),
            media_url=f"https://example.org/{title}/{days_ago}.mp3",
        )
    return show


def _podcast_episode_queries(ctx: CaptureQueriesContext) -> list[str]:
    return [
        q["sql"] for q in ctx.captured_queries if "catalog_podcastepisode" in q["sql"]
    ]


def test_latest_episode_is_the_newest_by_pub_date():
    show = _show("Nightly", ("Middle", 5), ("Newest", 1), ("Oldest", 30))

    episode = Podcast.objects.get(pk=show.pk).latest_episode

    assert episode is not None
    assert episode.title == "Newest"


def test_latest_episode_is_none_without_episodes():
    show = _show("Silent")

    assert Podcast.objects.get(pk=show.pk).latest_episode is None


def test_prefetch_latest_episodes_costs_one_query_for_any_number_of_shows():
    for i in range(3):
        _show(f"Show {i}", (f"Old {i}", 9), (f"New {i}", 2))
    _show("Empty")
    shows = list(Podcast.objects.order_by("pk"))

    with CaptureQueriesContext(connection) as ctx:
        Item.prefetch_latest_episodes(shows)
        titles = [s.latest_episode.title if s.latest_episode else None for s in shows]

    assert titles == ["New 0", "New 1", "New 2", None]
    # one DISTINCT ON pass covers the page, and reading the attached episodes
    # (including each show's title through ``episode.program``) adds nothing
    assert len(_podcast_episode_queries(ctx)) == 1


def test_prefetch_latest_episodes_ignores_other_item_types():
    book = Edition.objects.create(title="Not A Podcast")
    episode = _show("Hosting", ("Newest", 1)).latest_episode
    assert episode is not None

    with CaptureQueriesContext(connection) as ctx:
        Item.prefetch_latest_episodes([])
        Item.prefetch_latest_episodes([book, episode])

    # a page of books, or of episodes rather than shows, costs nothing
    assert _podcast_episode_queries(ctx) == []


def _collection_with_shows(username: str, count: int) -> tuple[Client, Collection]:
    user = User.register(email=f"{username}@example.com", username=username)
    client = Client()
    client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    collection = Collection.objects.create(
        owner=user.identity, title="Listening", visibility=0
    )
    for i in range(count):
        collection.append_item(_show(f"Show {i}", (f"Old {i}", 9), (f"New {i}", 2)))
    return client, collection


def test_collection_card_offers_the_latest_episode_for_playback():
    client, collection = _collection_with_shows("collector", 1)

    content = client.get(
        reverse("journal:collection_retrieve", args=[collection.uuid])
    ).content.decode()

    assert 'class="card-episode"' in content
    assert 'data-media="https://example.org/Show 0/2.mp3"' in content
    assert 'data-album="Show 0"' in content
    assert 'data-hosts="A Host"' in content
    # the title sits inside the play anchor, so clicking the words plays too
    assert re.search(r'<a class="episode"[^>]*>(?:(?!</a>).)*New 0</a>', content, re.S)
    # the markup alone is inert; podcast.js binds the click that starts playback
    assert "js/podcast.js" in content
    assert "shikwasa" in content


def test_collection_cards_do_not_query_per_podcast():
    client, collection = _collection_with_shows("flatcount", 4)
    url = reverse("journal:collection_retrieve", args=[collection.uuid])

    with CaptureQueriesContext(connection) as ctx:
        content = client.get(url).content.decode()

    assert content.count('class="card-episode"') == 4
    assert len(_podcast_episode_queries(ctx)) == 1


def test_item_page_sidebar_card_omits_the_episode_row():
    show = _show("Sidebarred", ("Newest", 1))

    content = Client().get(show.url).content.decode()

    # the page lists every episode already, so the compact sidebar card
    # (hide_brief) does not repeat the newest one
    assert 'class="card-episode"' not in content
