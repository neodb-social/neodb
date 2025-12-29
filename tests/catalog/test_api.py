from unittest.mock import patch

import pytest
import requests
from django.core.cache import cache
from django.utils import timezone

from catalog.models import (
    Album,
    Edition,
    Game,
    Movie,
    Performance,
    PerformanceProduction,
    Podcast,
    PodcastEpisode,
    TVEpisode,
    TVSeason,
    TVShow,
)


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_trending_tag_endpoint(live_server):
    cache.set("popular_tags", ["speculative", "noir"], timeout=None)

    response = requests.get(f"{live_server.url}/api/trending/tag/", timeout=5)

    assert response.status_code == 200
    payload = response.json()
    assert payload == ["speculative", "noir"]


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_book_api_includes_contents(live_server):
    with patch("catalog.models.item.Item.update_index"):
        edition = Edition.objects.create(title="API Book", contents="Chapter 1")

    response = requests.get(f"{live_server.url}/api/book/{edition.uuid}", timeout=5)

    assert response.status_code == 200
    payload = response.json()
    assert payload["uuid"] == edition.uuid
    assert payload["contents"] == "Chapter 1"


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_catalog_search_endpoint(live_server):
    with patch("catalog.models.item.Item.update_index"):
        edition = Edition.objects.create(title="Search Book")

    def fake_query_index(query, page, categories, prepare_external, exclude_categories):
        assert query == "Search"
        assert categories == ["book"]
        return [edition], 1, 1, None, None

    with patch("catalog.apis.query_index", side_effect=fake_query_index):
        response = requests.get(
            f"{live_server.url}/api/catalog/search?query=Search&category=book",
            timeout=5,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"][0]["uuid"] == edition.uuid

    response = requests.get(
        f"{live_server.url}/api/catalog/search?query=",
        timeout=5,
    )

    assert response.status_code == 400


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_catalog_fetch_endpoint_rejects_unsupported_url(live_server):
    with patch("catalog.apis.SiteManager.get_site_by_url", return_value=None):
        response = requests.get(
            f"{live_server.url}/api/catalog/fetch?url=http://example.com/none",
            timeout=5,
        )

    assert response.status_code == 422


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_catalog_fetch_endpoint_returns_redirect_when_item_found(live_server):
    with patch("catalog.models.item.Item.update_index"):
        edition = Edition.objects.create(title="Fetch Book")

    class StubSite:
        def get_item(self):
            return edition

    with patch("catalog.apis.SiteManager.get_site_by_url", return_value=StubSite()):
        response = requests.get(
            f"{live_server.url}/api/catalog/fetch?url=http://example.com/item",
            timeout=5,
            allow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["Location"] == edition.api_url


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_catalog_fetch_endpoint_returns_accepted_when_queued(live_server):
    class StubSite:
        def get_item(self):
            return None

    with (
        patch("catalog.apis.SiteManager.get_site_by_url", return_value=StubSite()),
        patch("catalog.apis.get_fetch_lock", return_value=True),
        patch("catalog.apis.enqueue_fetch") as enqueue_fetch,
    ):
        response = requests.get(
            f"{live_server.url}/api/catalog/fetch?url=http://example.com/queued",
            timeout=5,
        )

    assert response.status_code == 202
    enqueue_fetch.assert_called_once_with("http://example.com/queued", False)


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_catalog_trending_endpoints(live_server):
    with patch("catalog.models.item.Item.update_index"):
        book = Edition.objects.create(title="Trending Book")
        movie = Movie.objects.create(title="Trending Movie")
        show = TVShow.objects.create(title="Trending Show")
        album = Album.objects.create(title="Trending Album", artist=["Artist"])
        game = Game.objects.create(title="Trending Game")
        podcast = Podcast.objects.create(title="Trending Podcast", host=["Host"])
        performance = Performance.objects.create(title="Trending Performance")

    cache.set("trending_book", [book], timeout=None)
    cache.set("trending_movie", [movie], timeout=None)
    cache.set("trending_tv", [show], timeout=None)
    cache.set("trending_music", [album], timeout=None)
    cache.set("trending_game", [game], timeout=None)
    cache.set("trending_podcast", [podcast], timeout=None)
    cache.set("trending_performance", [performance], timeout=None)

    response = requests.get(f"{live_server.url}/api/trending/book/", timeout=5)
    assert response.status_code == 200
    assert response.json()[0]["uuid"] == book.uuid

    response = requests.get(f"{live_server.url}/api/trending/movie/", timeout=5)
    assert response.status_code == 200
    assert response.json()[0]["uuid"] == movie.uuid

    response = requests.get(f"{live_server.url}/api/trending/tv/", timeout=5)
    assert response.status_code == 200
    assert response.json()[0]["uuid"] == show.uuid

    response = requests.get(f"{live_server.url}/api/trending/music/", timeout=5)
    assert response.status_code == 200
    assert response.json()[0]["uuid"] == album.uuid

    response = requests.get(f"{live_server.url}/api/trending/game/", timeout=5)
    assert response.status_code == 200
    assert response.json()[0]["uuid"] == game.uuid

    response = requests.get(f"{live_server.url}/api/trending/podcast/", timeout=5)
    assert response.status_code == 200
    assert response.json()[0]["uuid"] == podcast.uuid

    response = requests.get(f"{live_server.url}/api/trending/performance/", timeout=5)
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["uuid"] == performance.uuid
    assert payload[0]["category"] == "performance"


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_book_sibling_endpoint(live_server):
    with patch("catalog.models.item.Item.update_index"):
        book1 = Edition.objects.create(
            title="Book One",
            localized_title=[{"lang": "en", "text": "Book One"}],
        )
        book2 = Edition.objects.create(
            title="Book Two",
            localized_title=[{"lang": "en", "text": "Book Two"}],
        )
        book1.link_to_related_book(book2)

    response = requests.get(
        f"{live_server.url}/api/book/{book1.uuid}/sibling/",
        timeout=5,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"][0]["uuid"] == book2.uuid


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_catalog_item_detail_endpoints(live_server):
    with patch("catalog.models.item.Item.update_index"):
        movie = Movie.objects.create(title="Test Movie")
        show = TVShow.objects.create(title="Test Show")
        season = TVSeason.objects.create(title="Season One", show=show, season_number=1)
        episode = TVEpisode.objects.create(
            title="Episode One", season=season, episode_number=1
        )
        podcast = Podcast.objects.create(title="Test Podcast", host=["Host"])
        podcast_episode = PodcastEpisode.objects.create(
            title="Episode One",
            program=podcast,
            pub_date=timezone.now(),
        )
        album = Album.objects.create(title="Test Album", artist=["Artist"])
        game = Game.objects.create(title="Test Game")
        performance = Performance.objects.create(title="Test Performance")
        production = PerformanceProduction.objects.create(
            title="Test Production", show=performance
        )

    endpoints = [
        (f"/api/movie/{movie.uuid}", movie.uuid, "movie"),
        (f"/api/tv/{show.uuid}", show.uuid, "tv"),
        (f"/api/tv/season/{season.uuid}", season.uuid, "tv"),
        (f"/api/tv/episode/{episode.uuid}", episode.uuid, "tv"),
        (f"/api/podcast/{podcast.uuid}", podcast.uuid, "podcast"),
        (
            f"/api/podcast/episode/{podcast_episode.uuid}",
            podcast_episode.uuid,
            "podcast",
        ),
        (f"/api/album/{album.uuid}", album.uuid, "music"),
        (f"/api/game/{game.uuid}", game.uuid, "game"),
        (f"/api/performance/{performance.uuid}", performance.uuid, "performance"),
        (
            f"/api/performance/production/{production.uuid}",
            production.uuid,
            "performance",
        ),
    ]

    for url, expected_uuid, expected_category in endpoints:
        response = requests.get(f"{live_server.url}{url}", timeout=5)
        assert response.status_code == 200
        payload = response.json()
        assert payload["uuid"] == expected_uuid
        assert payload["category"] == expected_category


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_podcast_episode_list_endpoint(live_server):
    with patch("catalog.models.item.Item.update_index"):
        podcast = Podcast.objects.create(title="List Podcast", host=["Host"])
        episode1 = PodcastEpisode.objects.create(
            title="Episode One",
            program=podcast,
            guid="ep-one",
            pub_date=timezone.now(),
        )
        episode2 = PodcastEpisode.objects.create(
            title="Episode Two",
            program=podcast,
            guid="ep-two",
            pub_date=timezone.now(),
        )

    response = requests.get(
        f"{live_server.url}/api/podcast/{podcast.uuid}/episode/",
        timeout=5,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    uuids = {ep["uuid"] for ep in payload["data"]}
    assert uuids == {episode1.uuid, episode2.uuid}

    response = requests.get(
        f"{live_server.url}/api/podcast/{podcast.uuid}/episode/?guid=ep-one",
        timeout=5,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["data"][0]["uuid"] == episode1.uuid
