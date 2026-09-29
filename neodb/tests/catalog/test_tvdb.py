import asyncio
import json
from io import StringIO
from pathlib import Path

import httpx
import pytest
from django.core.cache import cache
from django.core.management import call_command

from catalog.common import DownloadError, SiteManager, use_local_response
from catalog.common.downloaders import get_mock_file, set_mock_mode
from catalog.models import (
    ExternalResource,
    IdType,
    ItemCategory,
    Movie,
    People,
    TVEpisode,
    TVSeason,
    TVShow,
)
from catalog.sites import tvdb
from catalog.sites.tmdb import TMDB_TV, query_tmdb_tvdb_id
from catalog.sites.tvdb import (
    TVDB_Episode,
    TVDB_Movie,
    TVDB_Person,
    TVDB_Season,
    TVDB_Series,
    _credits,
    _language,
    _translations,
    _wanted,
    _slug_cache_key,
    _url_cache_key,
)
from common.models import SiteConfig

_TEST_DATA = Path(__file__).parent.parent.parent / "test_data"


def _fixture(url: str) -> dict:
    return json.loads((_TEST_DATA / get_mock_file(url)).read_text())


_SLUG_URLS = (
    "https://thetvdb.com/series/friends",
    "https://thetvdb.com/movies/the-matrix",
    "https://thetvdb.com/series/friends/seasons/official/2",
    "https://thetvdb.com/series/some-uncached-slug",
)


@pytest.fixture(autouse=True)
def _isolated():
    # use_local_response does not reset mock mode when a test raises, and the
    # test cache is the dev cluster's redis, which live fetches also fill
    set_mock_mode(False)
    for url in _SLUG_URLS:
        cache.delete(_url_cache_key(url))
    for kind, slug in (
        ("series", "friends"),
        ("movies", "the-matrix"),
        ("series", "some-uncached-slug"),
    ):
        cache.delete(_slug_cache_key(kind, slug))
    yield
    set_mock_mode(False)


@pytest.fixture
def mock_mode():
    # use_local_response cannot wrap tests that take fixtures
    set_mock_mode(True)
    yield
    set_mock_mode(False)


@pytest.fixture
def tvdb_key(monkeypatch):
    monkeypatch.setattr(SiteConfig.system, "tvdb_api_key", "test-key")


@pytest.fixture
def no_tvdb_key(monkeypatch):
    monkeypatch.setattr(SiteConfig.system, "tvdb_api_key", "")


class TestUrl:
    def test_series(self):
        cls = SiteManager.get_site_cls_by_id_type(IdType.TVDB_Series)
        assert cls is TVDB_Series
        for url in (
            "https://thetvdb.com/dereferrer/series/79168",
            "https://www.thetvdb.com/dereferrer/series/79168",
            "https://thetvdb.com/?tab=series&id=79168",
            "http://thetvdb.com/index.php?tab=series&id=79168",
        ):
            assert cls.validate_url(url), url
            assert cls.url_to_id(url) == "79168"
        assert cls.id_to_url("79168") == "https://thetvdb.com/dereferrer/series/79168"
        # slug urls never match by pattern alone: that would build a site
        # with no id on a cache miss
        assert not cls.validate_url("https://thetvdb.com/series/friends")

    def test_other_types(self):
        assert TVDB_Episode.url_to_id(
            "https://thetvdb.com/series/friends/episodes/303821"
        ) == ("303821")
        assert TVDB_Episode.url_to_id("https://thetvdb.com/dereferrer/episode/303821")
        assert TVDB_Episode.url_to_id(
            "https://thetvdb.com/?tab=episode&seriesid=79168&id=303821"
        ) == ("303821")
        assert TVDB_Season.url_to_id("https://thetvdb.com/dereferrer/season/16102") == (
            "16102"
        )
        assert TVDB_Movie.url_to_id("https://thetvdb.com/dereferrer/movie/169") == "169"
        assert TVDB_Person.url_to_id(
            "https://thetvdb.com/people/248045-james-burrows"
        ) == ("248045")
        assert TVDB_Person.id_to_url("248045") == "https://thetvdb.com/people/248045"
        assert not TVDB_Series.validate_url(
            "https://thetvdb.com/series/friends/episodes/303821"
        )

    @use_local_response
    def test_slug_urls_resolve_through_the_api(self):
        # the two steps SiteManager.get_site_by_url takes for a fallback url;
        # going through it would also run every other site's fallback, and
        # the Fediverse one reads the database
        for cls, url, tvdb_id in (
            (TVDB_Series, "https://thetvdb.com/series/friends", "79168"),
            (TVDB_Movie, "https://thetvdb.com/movies/the-matrix", "169"),
            (
                TVDB_Season,
                "https://thetvdb.com/series/friends/seasons/official/2",
                "16104",
            ),
        ):
            assert not cls.validate_url(url)
            assert cls.validate_url_fallback(url)
            site = cls(url)
            assert site.id_value == tvdb_id
            assert site.url == cls.id_to_url(tvdb_id)

    def test_slug_urls_are_inert_without_a_key(self, no_tvdb_key):
        url = "https://thetvdb.com/series/some-uncached-slug"
        assert not TVDB_Series.validate_url_fallback(url)
        assert TVDB_Series.url_to_id(url) is None


class TestHelpers:
    def test_language(self):
        assert _language("eng") == "en"
        assert _language("fra") == "fr"
        assert _language("zhtw") == "zh-tw"
        assert _language("yue") == "zh-hk"
        assert _language("pt") == "pt-br"
        assert _language("por") == "pt"
        assert _language("zho", "黑客帝国") == "zh-cn"
        assert _language("zho", "老友記") == "zh-tw"
        assert _language("xxx") is None
        assert _language(None) is None

    def test_credits(self):
        chars = [
            {"peopleId": 3, "personName": "C", "peopleType": "Actor", "sort": 2},
            {"peopleId": 1, "personName": "A", "peopleType": "Director"},
            {"peopleId": 2, "personName": "B", "peopleType": "Actor", "sort": 1},
            {"peopleId": 4, "personName": "D", "peopleType": "Executive Producer"},
            {"peopleId": 1, "personName": "A", "peopleType": "Director"},
            {"peopleId": 5, "personName": "E", "peopleType": "Guest Star"},
        ]
        c = _credits(chars)
        assert c["director"] == ["A"]
        assert c["actor"] == ["B", "C"]
        assert c["producer"] == ["D"]
        assert [p["id_value"] for p in c["related_people"]] == ["1", "4", "2", "3"]
        assert "url" not in c["related_people"][0]

    def test_original_language_keeps_its_variants(self, monkeypatch):
        monkeypatch.setattr(tvdb, "SITE_PREFERRED_LANGUAGES", ["en"])
        assert _wanted("zh-tw", "zh")
        assert _wanted("zh-cn", "zh")
        assert _wanted("en", "zh")
        assert not _wanted("fr", "zh")
        assert not _wanted("fr", None)

    def test_fallback_text_language_is_detected(self, monkeypatch):
        monkeypatch.setattr(tvdb, "SITE_PREFERRED_LANGUAGES", ["en"])
        record = {
            "name": "Attack on Titan",
            "overview": "Humanity fights the giant Titans behind the walls.",
            "translations": {
                "nameTranslations": [{"language": "jpn", "name": "進撃の巨人"}]
            },
        }
        titles, descs = _translations(record, "ja")
        assert {"lang": "ja", "text": "進撃の巨人"} in titles
        assert {"lang": "en", "text": "Attack on Titan"} in titles
        assert descs[0]["lang"] == "en"


class TestUnconfigured:
    def test_fetch_names_the_missing_setting(self, no_tvdb_key):
        site = TVDB_Series(id_value="79168")
        # DownloadError, so the linked-resource fetch after a TMDB or
        # Wikidata import logs a warning rather than an internal error
        with pytest.raises(DownloadError, match="API key"):
            site.scrape()

    def test_search_is_silent(self, no_tvdb_key):
        assert asyncio.run(TVDB_Series.search_task("friends", 1, "all", 5)) == []


class TestSearch:
    def test_search(self, tvdb_key, monkeypatch):
        data = _fixture("https://api4.thetvdb.com/v4/search?query=friends&limit=5")
        requests: list[tuple[str, dict]] = []

        async def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return httpx.Response(200, request=httpx.Request("GET", url), json=data)

        monkeypatch.setattr(httpx.AsyncClient, "get", get)
        monkeypatch.setattr(tvdb, "tvdb_token", lambda renew=False: "t")
        results = asyncio.run(TVDB_Series.search_task("friends", 2, "all", 5))
        url, kwargs = requests[0]
        assert url == "https://api4.thetvdb.com/v4/search"
        assert kwargs["params"] == {"query": "friends", "limit": 5, "offset": 5}
        assert kwargs["headers"]["Authorization"] == "Bearer t"
        assert results[0].category == ItemCategory.TV
        assert results[0].source_url == "https://thetvdb.com/dereferrer/series/79168"
        assert results[0].display_title == "Friends"
        assert results[1].category == ItemCategory.Movie
        assert results[1].source_url == "https://thetvdb.com/dereferrer/movie/41843"

        asyncio.run(TVDB_Series.search_task("friends", 1, "tv", 5))
        assert requests[1][1]["params"]["type"] == "series"
        assert asyncio.run(TVDB_Series.search_task("friends", 1, "book", 5)) == []
        assert len(requests) == 2


@pytest.mark.django_db(databases="__all__")
class TestScrape:
    @use_local_response
    def test_series(self):
        site = TVDB_Series(id_value="79168")
        site.get_resource_ready()
        assert site.resource is not None
        m = site.resource.metadata
        assert m["title"] == "Friends"
        assert m["orig_title"] == "Friends"
        assert {"lang": "en", "text": "Friends"} in m["localized_title"]
        assert m["release_date"] == "1994-09-22"
        assert m["origin_country"] == ["US"]
        assert m["language"] == ["en"]
        assert m["genre"] == ["Comedy"]
        assert m["season_count"] == 10
        assert m["single_episode_length"] == 22 * 60
        assert m["actor"][:2] == ["Jennifer Aniston", "Lisa Kudrow"]
        assert m["brief"].startswith("Rachel Green, Ross Geller")
        assert m["cover_image_url"].startswith("https://artworks.thetvdb.com/")
        seasons = [
            r for r in m["related_resources"] if r["id_type"] == IdType.TVDB_Season
        ]
        # official order only: seasons 0-10, no dvd or absolute duplicates
        assert len(seasons) == 11
        assert seasons[1]["id_value"] == "16102"
        assert site.resource.other_lookup_ids == {
            IdType.IMDB: "tt0108778",
            IdType.TMDB_TV: "1668",
        }
        assert isinstance(site.resource.item, TVShow)

    def test_localized_titles(self, mock_mode, monkeypatch):
        monkeypatch.setattr(tvdb, "SITE_PREFERRED_LANGUAGES", ["en", "zh"])
        m = TVDB_Series(id_value="79168").scrape().metadata
        titles = {(t["lang"], t["text"]) for t in m["localized_title"]}
        assert ("zh-tw", "六人行") in titles
        assert ("zh-hk", "老友記") in titles
        assert not any(t["lang"] == "fr" for t in m["localized_title"])

    @use_local_response
    def test_season(self):
        site = TVDB_Season(id_value="16104")
        site.get_resource_ready()
        assert site.resource is not None
        m = site.resource.metadata
        assert m["season_number"] == 2
        assert m["episode_count"] == 24
        assert m["episode_number_list"][:3] == [1, 2, 3]
        assert m["release_date"] == "1995-09-21"
        assert m["origin_country"] == ["US"]
        # the first episode's IMDB id, as Douban and TMDB_TVSeason file it
        assert site.resource.other_lookup_ids == {IdType.IMDB: "tt0583562"}
        # display_title is cached from before the show was linked
        item = TVSeason.objects.get(pk=site.resource.item.pk)
        assert item.show is not None
        assert item.show.display_title == "Friends"
        assert item.display_title == "Friends Season 2"

    @use_local_response
    def test_season_one_uses_show_imdb(self):
        site = TVDB_Season(id_value="16102")
        content = site.scrape()
        assert content.lookup_ids == {IdType.IMDB: "tt0108778"}

    @use_local_response
    def test_episode(self):
        site = TVDB_Episode(id_value="303821")
        content = site.scrape()
        m = content.metadata
        assert m["title"] == "The One Where Monica Gets a Roommate"
        assert m["season_number"] == 1
        assert m["episode_number"] == 1
        assert m["required_resources"][0]["id_value"] == "16102"
        assert content.lookup_ids == {IdType.IMDB: "tt0583459"}
        site.get_resource_ready()
        assert site.resource is not None
        assert isinstance(site.resource.item, TVEpisode)

    @use_local_response
    def test_movie(self):
        site = TVDB_Movie(id_value="169")
        site.get_resource_ready()
        assert site.resource is not None
        m = site.resource.metadata
        assert m["title"] == "The Matrix"
        assert m["release_date"] == "1999-03-31"
        assert m["length"] == 136 * 60
        assert m["origin_country"] == ["US"]
        assert m["language"] == ["en"]
        assert m["genre"] == ["Action", "Science Fiction"]
        assert m["actor"][:3] == ["Keanu Reeves", "Carrie-Anne Moss", "Hugo Weaving"]
        assert m["director"]
        assert m["brief"].startswith("In the 22nd Century")
        assert site.resource.other_lookup_ids == {
            IdType.IMDB: "tt0133093",
            IdType.TMDB_Movie: "603",
            IdType.WikiData: "Q83495",
        }
        assert isinstance(site.resource.item, Movie)

    @use_local_response
    def test_person(self):
        site = TVDB_Person(id_value="304377")
        site.get_resource_ready()
        assert site.resource is not None
        m = site.resource.metadata
        assert m["title"] == "Jennifer Aniston"
        assert {"lang": "en", "text": "Jennifer Aniston"} in m["localized_name"]
        assert site.resource.other_lookup_ids == {
            IdType.IMDB: "nm0000098",
            IdType.TMDB_Person: "4491",
            IdType.WikiData: "Q32522",
        }
        assert isinstance(site.resource.item, People)
        urls = site.fetch_people_work_urls()
        assert "https://thetvdb.com/dereferrer/series/79168" in urls
        assert any("/dereferrer/movie/" in u for u in urls)


@pytest.mark.django_db(databases="__all__")
class TestTMDBLink:
    @use_local_response
    def test_tmdb_carries_tvdb_ids(self):
        content = TMDB_TV(id_value="1668").scrape()
        assert content.lookup_ids[IdType.TVDB_Series] == "79168"

    @use_local_response
    def test_tvdb_lands_on_the_tmdb_item(self):
        tmdb = TMDB_TV(id_value="1668")
        tmdb.get_resource_ready()
        assert tmdb.resource is not None
        site = TVDB_Series(id_value="79168")
        site.get_resource_ready()
        assert site.resource is not None
        assert site.resource.item == tmdb.resource.item
        assert TVShow.objects.count() == 1

    @use_local_response
    def test_query_tmdb_tvdb_id(self):
        assert query_tmdb_tvdb_id(IdType.TMDB_TV, "1668") == "79168"
        assert query_tmdb_tvdb_id(IdType.TMDB_TVSeason, "1668-2") == "16104"
        assert query_tmdb_tvdb_id(IdType.TMDB_TVEpisode, "1668-2-1") == "303845"
        assert query_tmdb_tvdb_id(IdType.TMDB_Movie, "603") is None
        assert query_tmdb_tvdb_id(IdType.TMDB_TVSeason, "1668") is None

    def test_backfill_command(self, mock_mode, tvdb_key):
        show = ExternalResource.objects.create(
            id_type=IdType.TMDB_TV,
            id_value="1668",
            url="https://www.themoviedb.org/tv/1668",
            other_lookup_ids={IdType.IMDB: "tt0108778"},
        )
        season = ExternalResource.objects.create(
            id_type=IdType.TMDB_TVSeason,
            id_value="1668-2",
            url="https://www.themoviedb.org/tv/1668/season/2",
        )
        call_command("catalog", "tvdb-tmdb", "--dry-run")
        show.refresh_from_db()
        assert IdType.TVDB_Series not in show.other_lookup_ids

        call_command("catalog", "tvdb-tmdb")
        show.refresh_from_db()
        season.refresh_from_db()
        assert show.other_lookup_ids == {
            IdType.IMDB: "tt0108778",
            IdType.TVDB_Series: "79168",
        }
        assert season.other_lookup_ids == {IdType.TVDB_Season: "16104"}
        assert ExternalResource.objects.filter(
            id_type=IdType.TVDB_Series, id_value="79168"
        ).exists()
        assert ExternalResource.objects.filter(
            id_type=IdType.TVDB_Season, id_value="16104"
        ).exists()

    def test_backfill_retries_a_stored_id_without_tmdb(
        self, mock_mode, tvdb_key, monkeypatch
    ):
        """An id stored by an earlier run (failed fetch, or no key yet) is
        fetched again, straight from the stored id."""

        def no_tmdb(*args):
            raise AssertionError("TMDB must not be queried for a stored id")

        monkeypatch.setattr(
            "catalog.management.commands.catalog.query_tmdb_tvdb_id", no_tmdb
        )
        ExternalResource.objects.create(
            id_type=IdType.TMDB_TV,
            id_value="1668",
            url="https://www.themoviedb.org/tv/1668",
            other_lookup_ids={IdType.TVDB_Series: "79168"},
        )
        out = StringIO()
        call_command("catalog", "tvdb-tmdb", stdout=out)
        assert "TheTVDB resources linked: 1" in out.getvalue()
        assert ExternalResource.objects.filter(
            id_type=IdType.TVDB_Series, id_value="79168"
        ).exists()
        # linked now, so a rerun selects nothing
        out = StringIO()
        call_command("catalog", "tvdb-tmdb", stdout=out)
        assert "TheTVDB resources linked: 0" in out.getvalue()

    def test_backfill_without_key_skips_stored_ids(
        self, mock_mode, no_tvdb_key, monkeypatch
    ):
        def no_tmdb(*args):
            raise AssertionError("TMDB must not be queried for a stored id")

        monkeypatch.setattr(
            "catalog.management.commands.catalog.query_tmdb_tvdb_id", no_tmdb
        )
        ExternalResource.objects.create(
            id_type=IdType.TMDB_TV,
            id_value="1668",
            url="https://www.themoviedb.org/tv/1668",
            other_lookup_ids={IdType.TVDB_Series: "79168"},
        )
        call_command("catalog", "tvdb-tmdb")
        assert not ExternalResource.objects.filter(id_type=IdType.TVDB_Series).exists()
