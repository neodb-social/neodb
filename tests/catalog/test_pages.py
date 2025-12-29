from datetime import datetime, timezone

import pytest
import requests

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
def test_catalog_item_pages(live_server):
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
        response = requests.get(f"{live_server.url}{item.url}", timeout=5)
        assert response.status_code == 200


@pytest.mark.django_db(databases="__all__", transaction=True)
def test_catalog_discover_and_search_pages(live_server):
    response = requests.get(f"{live_server.url}/discover/", timeout=5)
    assert response.status_code == 200

    response = requests.get(f"{live_server.url}/search?c=book", timeout=5)
    assert response.status_code == 200
