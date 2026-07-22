import asyncio
import time

import httpx
import pytest
import requests
from django.conf import settings
from igdb.wrapper import IGDBWrapper

from catalog.common import *
from catalog.models import Game, IdType, SiteName
from catalog.sites.igdb import IGDB, igdb_limiter


@pytest.mark.django_db(databases="__all__")
class TestIGDB:
    def test_parse(self):
        t_id_type = IdType.IGDB
        t_id_value = "portal-2"
        t_url = "https://www.igdb.com/games/portal-2"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.url == t_url
        assert site.id_value == t_id_value

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.igdb.com/games/portal-2"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Portal 2"
        print(site.resource.other_lookup_ids)
        print(site.resource.metadata)
        assert isinstance(site.resource.item, Game)
        assert site.resource.item.steam == "620"
        assert site.resource.item.genre == [
            "shooter",
            "platformer",
            "puzzle",
            "adventure",
        ]

    @use_local_response
    def test_scrape_non_steam(self):
        t_url = "https://www.igdb.com/games/the-legend-of-zelda-breath-of-the-wild"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert (
            site.resource.metadata["title"] == "The Legend of Zelda: Breath of the Wild"
        )
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Game)
        assert site.resource.item.primary_lookup_id_type == IdType.IGDB
        assert site.resource.item.genre == ["puzzle", "rpg", "adventure"]
        assert (
            site.resource.item.primary_lookup_id_value
            == "the-legend-of-zelda-breath-of-the-wild"
        )

    def test_api_query_handles_429_without_crashing(self, monkeypatch):
        # IGDBWrapper raises requests' HTTPError (not httpx's) on a 429;
        # api_query must catch it and degrade to [] rather than let it
        # propagate and crash the caller (e.g. a Steam import job).
        # Regression for EGGPLANT-1GY / EGGPLANT-1GZ.
        monkeypatch.setattr(igdb_limiter(), "acquire", lambda timeout: None)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        def _raise_429(self, endpoint, query):
            response = requests.Response()
            response.status_code = 429
            raise requests.exceptions.HTTPError("429 Client Error", response=response)

        monkeypatch.setattr(IGDBWrapper, "api_request", _raise_429)
        assert IGDB.api_query("games", "fields *;") == []

    def test_api_query_retries_429_then_succeeds(self, monkeypatch):
        # A 429 can still slip past the limiter (Redis unreachable, or the
        # queue exceeded acquire()'s timeout); a bounded retry that honors
        # Retry-After should recover the item instead of losing its data.
        monkeypatch.setattr(igdb_limiter(), "acquire", lambda timeout: None)
        sleeps = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

        calls = {"n": 0}

        def _flaky(self, endpoint, query):
            calls["n"] += 1
            if calls["n"] == 1:
                response = requests.Response()
                response.status_code = 429
                response.headers["Retry-After"] = "2"
                raise requests.exceptions.HTTPError(
                    "429 Client Error", response=response
                )
            return b'[{"id": 1}]'

        monkeypatch.setattr(IGDBWrapper, "api_request", _flaky)
        assert IGDB.api_query("games", "fields *;") == [{"id": 1}]
        assert sleeps == [2.0]

    def test_search_task_429_records_failure(self, monkeypatch):
        # search_task never called raise_for_status(), so a 429 silently
        # fell through to a cacheable empty result with no failure
        # telemetry. It must now be caught and recorded like any other
        # transient IGDB failure.
        async def _noop_acquire(timeout=15.0):
            return None

        monkeypatch.setattr(igdb_limiter(), "acquire_async", _noop_acquire)

        async def _fake_post(self, url, **kwargs):
            return httpx.Response(429, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

        failures = []
        monkeypatch.setattr(
            "catalog.sites.igdb.record_search_failure",
            lambda site, reason: failures.append((site, reason)),
        )

        results = asyncio.run(
            IGDB.search_task("test", page=1, category="game", page_size=5)
        )
        assert results == []
        assert failures == [(SiteName.IGDB.value, "error")]


@pytest.mark.django_db(databases="__all__")
class TestSteam:
    def test_parse(self):
        t_id_type = IdType.Steam
        t_id_value = "620"
        t_url = "https://store.steampowered.com/app/620/Portal_2/"
        t_url2 = "https://store.steampowered.com/app/620"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.url == t_url2
        assert site.id_value == t_id_value

    @use_local_response
    def test_scrape(self):
        t_url = "https://store.steampowered.com/app/620/Portal_2/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Portal 2"
        assert site.resource.metadata["brief"][:6] == "Sequel"
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Game)
        assert site.resource.item.steam == "620"
        assert site.resource.item.genre == [
            "shooter",
            "platformer",
            "puzzle",
            "adventure",
        ]
        # header.jpg is horizontal; vertical covers are preferred, falling
        # through library_600x900_2x (absent in test data) to library_600x900
        assert "library_600x900.jpg" in site.resource.metadata["cover_image_url"]
        assert site.resource.cover.name != settings.DEFAULT_ITEM_COVER


@pytest.mark.django_db(databases="__all__")
class TestItch:
    @use_local_response
    def test_parse(self):
        t_url = "https://william-rous.itch.io/type-help"
        t_embed = "https://itch.io/embed/3268593"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.url == t_url
        assert site.id_value == "games/3268593"

        site3 = SiteManager.get_site_by_url(t_embed)
        assert site3 is not None
        assert site3.id_value == "games/3268593"

    @use_local_response
    def test_scrape(self):
        t_url = "https://william-rous.itch.io/type-help"
        t_embed = "https://itch.io/embed/3268593"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Type Help"
        assert site.resource.id_value == "games/3268593"
        assert site.resource.other_lookup_ids.get(IdType.Itch) == "games/3268593"
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Game)
        assert site.resource.item.itch == "games/3268593"
        assert site.resource.item.platform == ["web"]
        assert site.resource.item.display_description.startswith(
            "The Unsolvable Mystery"
        )
        assert "A puzzle-mystery game inspired by Return of the Obra Dinn" in (
            site.resource.item.display_description
        )

        embed_site = SiteManager.get_site_by_url(t_embed)
        assert embed_site is not None
        embed_res = embed_site.get_resource_ready()
        assert embed_res is not None
        assert embed_res.item is not None
        assert embed_res.item.pk == site.resource.item.pk
        assert embed_res.url == t_url
        assert embed_res.id_value == "games/3268593"


@pytest.mark.django_db(databases="__all__")
class TestDoubanGame:
    def test_parse(self):
        t_id_type = IdType.DoubanGame
        t_id_value = "10734307"
        t_url = "https://www.douban.com/game/10734307/"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.url == t_url
        assert site.id_value == t_id_value

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.douban.com/game/10734307/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Game)
        titles = sorted([t["text"] for t in site.resource.item.localized_title])
        assert titles == ["Portal 2", "传送门2"]
        assert site.resource.item.douban_game == "10734307"
        assert site.resource.item.genre == ["shooter", "puzzle"]


@pytest.mark.django_db(databases="__all__")
class TestBangumiGame:
    @use_local_response
    def test_parse(self):
        t_id_type = IdType.Bangumi
        t_id_value = "15912"
        t_url = "https://bgm.tv/subject/15912"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.url == t_url
        assert site.id_value == t_id_value
        resource = site.get_resource_ready()
        assert resource is not None
        assert resource.item is not None
        i = resource.item
        assert isinstance(i, Game)
        assert i.genre == ["PUZ"]
        site2 = SiteManager.get_site_by_url("https://bgm.tv/subject/228086")
        assert site2 is not None
        resource2 = site2.get_resource_ready()
        assert resource2 is not None
        assert resource2.item is not None
        i = resource2.item
        assert isinstance(i, Game)
        assert i.genre == ["ADV", "Psychological Horror"]


@pytest.mark.django_db(databases="__all__")
class TestBoardGameGeek:
    @use_local_response
    def test_scrape(self):
        t_url = "https://boardgamegeek.com/boardgame/167791"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.ID_TYPE == IdType.BGG
        assert site.id_value == "167791"
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Game)

        # TODO this fails occasionally bc languagedetect flips coin
        # assert site.resource.item.display_title == "Terraforming Mars"

        assert len(site.resource.item.localized_title) == 16
        assert isinstance(site.resource.item, Game)
        assert site.resource.item.platform == ["boardgame"]
        assert site.resource.item.genre[0] == "Economic"
        assert site.resource.item.designer == ["Jacob Fryxelius"]


@pytest.mark.django_db(databases="__all__")
class TestMobyGames:
    def test_parse(self):
        t_id_type = IdType.MobyGames
        t_id_value = "51233"
        t_url = "https://www.mobygames.com/game/51233/portal-2/"
        t_url_canonical = "https://www.mobygames.com/game/51233/"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)
        assert site.validate_url(t_url_canonical)
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.url == t_url_canonical
        assert site.id_value == t_id_value

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.mobygames.com/game/51233/portal-2/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Portal 2"
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Game)
        assert site.resource.item.developer == ["Valve Corporation"]
        assert "action" in site.resource.item.genre
        assert "windows" in site.resource.item.platform


@pytest.mark.django_db(databases="__all__")
class TestMultiGameSites:
    @use_local_response
    def test_games(self):
        url1 = "https://www.igdb.com/games/portal-2"
        url2 = "https://store.steampowered.com/app/620/Portal_2/"
        url3 = "https://www.wikidata.org/wiki/Q279446"
        site1 = SiteManager.get_site_by_url(url1)
        assert site1 is not None
        p1 = site1.get_resource_ready()
        assert p1 is not None
        assert p1.item is not None
        site2 = SiteManager.get_site_by_url(url2)
        assert site2 is not None
        p2 = site2.get_resource_ready()
        assert p2 is not None
        assert p2.item is not None
        assert isinstance(p1.item, Game)
        assert isinstance(p2.item, Game)
        assert p1.item == p2.item
        site3 = SiteManager.get_site_by_url(url3)
        assert site3 is not None
        p3 = site3.get_resource_ready()
        assert p3 is not None
        assert p3.item == p2.item
