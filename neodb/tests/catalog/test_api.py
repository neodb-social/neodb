from datetime import timedelta
from unittest.mock import ANY, patch

import pytest
import requests
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from catalog.apis import get_episodes_in_podcast
from catalog.models import (
    Album,
    Edition,
    Game,
    ItemCredit,
    Movie,
    People,
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
    enqueue_fetch.assert_called_once_with("http://example.com/queued", False, ANY)


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_catalog_fetch_endpoint_resolves_local_url(live_server):
    with patch("catalog.models.item.Item.update_index"):
        movie = Movie.objects.create(title="Local Movie")

    with patch("catalog.apis.enqueue_fetch") as enqueue_fetch:
        response = requests.get(
            f"{live_server.url}/api/catalog/fetch",
            params={"url": movie.absolute_url},
            timeout=5,
            allow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["Location"] == movie.api_url
    assert response.json()["url"] == movie.api_url
    enqueue_fetch.assert_not_called()


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_catalog_fetch_endpoint_resolves_local_alternative_url(live_server):
    """The federated `/~neodb~/` prefix and the API path resolve too."""
    with patch("catalog.models.item.Item.update_index"):
        movie = Movie.objects.create(title="Local Movie")

    site_url = settings.SITE_INFO["site_url"]
    for url in [
        f"{site_url}/~neodb~{movie.url}",
        f"{site_url}{movie.api_url}",
        f"{site_url}{movie.url}/",
    ]:
        response = requests.get(
            f"{live_server.url}/api/catalog/fetch",
            params={"url": url},
            timeout=5,
            allow_redirects=False,
        )
        assert response.status_code == 302, url
        assert response.headers["Location"] == movie.api_url


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_catalog_fetch_endpoint_resolves_merged_local_url(live_server):
    with patch("catalog.models.item.Item.update_index"):
        movie = Movie.objects.create(title="Local Movie")
        merged = Movie.objects.create(title="Merged Movie", merged_to_item=movie)

    response = requests.get(
        f"{live_server.url}/api/catalog/fetch",
        params={"url": merged.absolute_url},
        timeout=5,
        allow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"] == movie.api_url


@pytest.mark.django_db(databases="__all__", transaction=True)
@pytest.mark.parametrize("path", ["/movie/nonexistentitemuuid1", "/users/someone/"])
def test_catalog_fetch_endpoint_returns_not_found_for_local_url(live_server, path):
    response = requests.get(
        f"{live_server.url}/api/catalog/fetch",
        params={"url": f"{settings.SITE_INFO['site_url']}{path}"},
        timeout=5,
        allow_redirects=False,
    )

    assert response.status_code == 404


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_catalog_fetch_endpoint_returns_not_found_for_deleted_local_item(live_server):
    with patch("catalog.models.item.Item.update_index"):
        movie = Movie.objects.create(title="Deleted Movie")
        movie.is_deleted = True
        movie.save()

    response = requests.get(
        f"{live_server.url}/api/catalog/fetch",
        params={"url": movie.absolute_url},
        timeout=5,
        allow_redirects=False,
    )

    assert response.status_code == 404


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


@pytest.mark.django_db(databases="__all__")
def test_podcast_episode_list_is_paginated_in_database():
    """The endpoint must not materialize every episode before slicing."""
    now = timezone.now()
    with patch("catalog.models.item.Item.update_index"):
        podcast = Podcast.objects.create(title="Large Podcast", host=["Host"])
        for i in range(25):
            PodcastEpisode.objects.create(
                title=f"Episode {i}",
                program=podcast,
                guid=f"large-episode-{i}",
                pub_date=now - timedelta(days=i),
            )

    request = RequestFactory().get(f"/api/podcast/{podcast.uuid}/episode/")
    with CaptureQueriesContext(connection) as ctx:
        payload = get_episodes_in_podcast(
            request, str(podcast.uuid), HttpResponse(), page=1
        )

    assert payload["count"] == 25
    assert payload["pages"] == 2
    assert len(payload["data"]) == 20

    payload = get_episodes_in_podcast(
        request, str(podcast.uuid), HttpResponse(), page=3
    )
    assert payload["data"] == []

    episode_queries = [
        q["sql"] for q in ctx.captured_queries if "catalog_podcastepisode" in q["sql"]
    ]
    count_queries = [q for q in episode_queries if "COUNT(" in q.upper()]
    page_queries = [
        q for q in episode_queries if "COUNT(" not in q.upper() and "LIMIT 20" in q
    ]
    assert len(count_queries) == 1, episode_queries
    assert page_queries, episode_queries


@pytest.mark.django_db(databases="__all__")
def test_empty_podcast_episode_list_has_zero_pages():
    with patch("catalog.models.item.Item.update_index"):
        podcast = Podcast.objects.create(title="Empty Podcast", host=["Host"])

    response = Client().get(f"/api/podcast/{podcast.uuid}/episode/")

    assert response.status_code == 200
    assert response.json() == {"data": [], "pages": 0, "count": 0}


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_people_detail_endpoint(live_server):
    with patch("catalog.models.item.Item.update_index"):
        person = People.objects.create(
            localized_name=[{"lang": "en", "text": "API Person"}],
            people_type="person",
        )

    response = requests.get(f"{live_server.url}/api/people/{person.uuid}", timeout=5)

    assert response.status_code == 200
    payload = response.json()
    assert payload["uuid"] == person.uuid
    assert payload["display_name"] == "API Person"
    assert payload["api_url"] == f"/api/people/{person.uuid}"


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_item_credit_endpoints_include_linked_and_name_only_credits(live_server):
    with patch("catalog.models.item.Item.update_index"):
        movie = Movie.objects.create(title="Credited Movie")
        person = People.objects.create(
            localized_name=[{"lang": "en", "text": "Linked Actor"}]
        )
        ItemCredit.objects.create(
            item=movie,
            person=person,
            role="actor",
            name="Linked Actor",
            character_name="Lead",
        )
        ItemCredit.objects.create(
            item=movie, role="director", name="Name Only Director"
        )

    for item_type in ("item", "movie"):
        response = requests.get(
            f"{live_server.url}/api/catalog/{item_type}/{movie.uuid}/credit/",
            timeout=5,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2
        credits = {credit["name"]: credit for credit in payload["data"]}
        assert credits["Linked Actor"]["role"] == "actor"
        assert credits["Linked Actor"]["character_name"] == "Lead"
        assert credits["Linked Actor"]["person"]["uuid"] == person.uuid
        assert credits["Name Only Director"]["person"] is None

    response = requests.get(
        f"{live_server.url}/api/catalog/book/{movie.uuid}/credit/",
        timeout=5,
        allow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"] == (f"/api/catalog/movie/{movie.uuid}/credit/")


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_item_api_localizes_credit_names(live_server):
    """credits[].name follows the request language, as /api/people/{uuid} does.

    Names frozen at sync time gave an English reader a localized title next to
    a Chinese director. Credits with no linked person stay frozen.
    """
    zh_name = "沃什·威斯特摩兰"
    with patch("catalog.models.item.Item.update_index"):
        movie = Movie.objects.create(
            title="Obsession",
            localized_title=[{"lang": "en", "text": "Obsession"}],
        )
        person = People.objects.create(
            localized_name=[
                {"lang": "zh-cn", "text": zh_name},
                {"lang": "en", "text": "Wash Westmoreland"},
            ],
            people_type="person",
        )
        ItemCredit.objects.create(
            item=movie, person=person, role="director", name=zh_name
        )
        ItemCredit.objects.create(item=movie, role="actor", name="Name Only Actor")

    def detail(lang: str) -> dict:
        response = requests.get(
            f"{live_server.url}/api/movie/{movie.uuid}",
            headers={"Accept-Language": lang},
            timeout=5,
        )
        assert response.status_code == 200
        payload = response.json()
        payload["credit_names"] = {c["role"]: c["name"] for c in payload["credits"]}
        return payload

    assert detail("en")["credit_names"]["director"] == "Wash Westmoreland"
    assert detail("zh-Hans")["credit_names"]["director"] == zh_name
    assert detail("en")["credit_names"]["actor"] == "Name Only Actor"
    # The per-role field must agree with credits[] in the same payload.
    assert detail("en")["director"] == ["Wash Westmoreland"]
    assert detail("zh-Hans")["director"] == [zh_name]

    # ap_object (activity+json, and the payload catalog backups store) is
    # canonical: it keeps the frozen snapshot whatever the reader asks for.
    response = requests.get(
        f"{live_server.url}{movie.url}",
        headers={"Accept": "application/activity+json", "Accept-Language": "en"},
        timeout=5,
    )
    assert response.status_code == 200
    ap = response.json()
    assert {c["role"]: c["name"] for c in ap["credits"]}["director"] == zh_name
    assert ap["director"] == [zh_name]

    # The credit listing endpoint localizes too: its name used to disagree with
    # the person.display_name embedded in the same object.
    response = requests.get(
        f"{live_server.url}/api/catalog/movie/{movie.uuid}/credit/?lang=en",
        timeout=5,
    )
    assert response.status_code == 200
    listed = {c["role"]: c for c in response.json()["data"]}
    assert listed["director"]["name"] == "Wash Westmoreland"
    assert listed["director"]["person"]["display_name"] == "Wash Westmoreland"
    assert listed["actor"]["name"] == "Name Only Actor"

    # ... and so does the person's work listing.
    response = requests.get(
        f"{live_server.url}/api/people/{person.uuid}/work/",
        headers={"Accept-Language": "en"},
        timeout=5,
    )
    assert response.status_code == 200
    works = {entry["item"]["uuid"]: entry for entry in response.json()["data"]}
    assert [c["name"] for c in works[movie.uuid]["credits"]] == ["Wash Westmoreland"]


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_people_work_endpoint_groups_all_credits_by_item(live_server):
    with patch("catalog.models.item.Item.update_index"):
        person = People.objects.create(
            localized_name=[{"lang": "en", "text": "Multi-role Person"}]
        )
        movie = Movie.objects.create(title="Movie Work")
        book = Edition.objects.create(title="Book Work")
        ItemCredit.objects.create(
            item=movie, person=person, role="actor", name="Multi-role Person"
        )
        ItemCredit.objects.create(
            item=movie, person=person, role="director", name="Multi-role Person"
        )
        ItemCredit.objects.create(
            item=book, person=person, role="author", name="Multi-role Person"
        )

    response = requests.get(
        f"{live_server.url}/api/people/{person.uuid}/work/", timeout=5
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    works = {entry["item"]["uuid"]: entry for entry in payload["data"]}
    assert {credit["role"] for credit in works[movie.uuid]["credits"]} == {
        "actor",
        "director",
    }
    assert [credit["role"] for credit in works[book.uuid]["credits"]] == ["author"]
