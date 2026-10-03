from datetime import datetime, timezone

import pytest
from django.test import Client

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
from users.models import User


@pytest.mark.django_db(databases="__all__")
def test_catalog_item_pages(client: Client) -> None:
    book = Edition.objects.create(title="Web Book")
    movie = Movie.objects.create(title="Web Movie")
    show = TVShow.objects.create(title="Web Show")
    season = TVSeason.objects.create(title="Web Season", show=show, season_number=1)
    episode = TVEpisode.objects.create(
        title="Web Episode", season=season, episode_number=1
    )
    album = Album.objects.create(title="Web Album", artist=["Artist"])
    game = Game.objects.create(title="Web Game")
    podcast = Podcast.objects.create(title="Web Podcast", host=["Host"])
    podcast_episode = PodcastEpisode.objects.create(
        title="Web Podcast Episode",
        program=podcast,
        pub_date=datetime.now(tz=timezone.utc),
    )
    performance = Performance.objects.create(title="Web Performance")
    production = PerformanceProduction.objects.create(
        title="Web Production", show=performance
    )

    items = [
        book,
        movie,
        show,
        season,
        episode,
        album,
        game,
        podcast,
        podcast_episode,
        performance,
        production,
    ]
    for item in items:
        response = client.get(item.url, follow=True)
        assert response.status_code == 200
        assert item.display_title in response.content.decode()


@pytest.mark.django_db(databases="__all__")
def test_catalog_discover(client: Client) -> None:
    response = client.get("/discover/", follow=True)
    assert response.status_code == 200

    user = User.register(email="searcher@example.com", username="searcher")
    authed_client = Client()
    authed_client.force_login(user, backend="mastodon.auth.OAuth2Backend")
    response = authed_client.get("/discover/", follow=True)
    assert response.status_code == 200


@pytest.mark.django_db(databases="__all__")
def test_catalog_search(client: Client) -> None:
    user = User.register(email="searcher@example.com", username="searcher")
    authed_client = Client()
    authed_client.force_login(user, backend="mastodon.auth.OAuth2Backend")

    book = Edition.objects.create(
        localized_title=[{"lang": "en", "text": "Searchable Book"}]
    )
    movie = Movie.objects.create(
        localized_title=[{"lang": "en", "text": "Searchable movie"}]
    )

    response = client.get("/search?q=Searchable", follow=True)
    assert response.status_code == 200
    assert book.url in response.content.decode()
    assert movie.url in response.content.decode()

    response = authed_client.get("/search?c=book&q=Searchable", follow=True)
    assert response.status_code == 200
    assert book.url in response.content.decode()

    # not testing the actual external search, just that the page loads
    response = authed_client.get("/search/external?c=book", follow=True)
    assert response.status_code == 200
