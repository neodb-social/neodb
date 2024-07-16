from django.test import TestCase

from catalog.common import *
from catalog.models import *


class IGDBTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_id_type = IdType.IGDB
        t_id_value = "portal-2"
        t_url = "https://www.igdb.com/games/portal-2"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url)
        self.assertEqual(site.id_value, t_id_value)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.igdb.com/games/portal-2"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "Portal 2")
        self.assertIsInstance(site.resource.item, Game)
        self.assertEqual(site.resource.item.steam, "620")
        self.assertEqual(
            site.resource.item.genre, ["Shooter", "Platform", "Puzzle", "Adventure"]
        )

    @use_local_response
    def test_scrape_non_steam(self):
        t_url = "https://www.igdb.com/games/the-legend-of-zelda-breath-of-the-wild"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(
            site.resource.metadata["title"], "The Legend of Zelda: Breath of the Wild"
        )
        self.assertIsInstance(site.resource.item, Game)
        self.assertEqual(site.resource.item.primary_lookup_id_type, IdType.IGDB)
        self.assertEqual(
            site.resource.item.genre, ["Puzzle", "Role-playing (RPG)", "Adventure"]
        )
        self.assertEqual(
            site.resource.item.primary_lookup_id_value,
            "the-legend-of-zelda-breath-of-the-wild",
        )


class SteamTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_id_type = IdType.Steam
        t_id_value = "620"
        t_url = "https://store.steampowered.com/app/620/Portal_2/"
        t_url2 = "https://store.steampowered.com/app/620"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url2)
        self.assertEqual(site.id_value, t_id_value)

    @use_local_response
    def test_scrape(self):
        t_url = "https://store.steampowered.com/app/620/Portal_2/"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "Portal 2")
        self.assertEqual(site.resource.metadata["brief"][:6], "Sequel")
        self.assertIsInstance(site.resource.item, Game)
        self.assertEqual(site.resource.item.steam, "620")
        self.assertEqual(
            site.resource.item.genre, ["Shooter", "Platform", "Puzzle", "Adventure"]
        )


class DoubanGameTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_id_type = IdType.DoubanGame
        t_id_value = "10734307"
        t_url = "https://www.douban.com/game/10734307/"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url)
        self.assertEqual(site.id_value, t_id_value)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.douban.com/game/10734307/"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertIsInstance(site.resource.item, Game)
        titles = sorted([t["text"] for t in site.resource.item.localized_title])
        self.assertEqual(titles, ["Portal 2", "传送门2"])
        self.assertEqual(site.resource.item.douban_game, "10734307")
        self.assertEqual(site.resource.item.genre, ["第一人称射击", "益智"])
        self.assertEqual(site.resource.item.other_title, [])


class BangumiGameTestCase(TestCase):
    databases = "__all__"

    # @use_local_response
    def test_parse(self):
        t_id_type = IdType.Bangumi
        t_id_value = "15912"
        t_url = "https://bgm.tv/subject/15912"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url)
        self.assertEqual(site.id_value, t_id_value)
        i = site.get_resource_ready().item
        self.assertEqual(i.genre, ["PUZ"])
        i = (
            SiteManager.get_site_by_url("https://bgm.tv/subject/228086")
            .get_resource_ready()
            .item
        )
        self.assertEqual(i.genre, ["ADV", "Psychological Horror"])


class BoardGameGeekTestCase(TestCase):
    databases = "__all__"

    @use_local_response
    def test_scrape(self):
        t_url = "https://boardgamegeek.com/boardgame/167791"
        site = SiteManager.get_site_by_url(t_url)
        self.assertIsNotNone(site)
        self.assertEqual(site.ID_TYPE, IdType.BGG)
        self.assertEqual(site.id_value, "167791")
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertIsInstance(site.resource.item, Game)

        # TODO this fails occasionally bc languagedetect flips coin
        # self.assertEqual(site.resource.item.display_title, "Terraforming Mars")

        self.assertEqual(len(site.resource.item.localized_title), 16)
        self.assertEqual(site.resource.item.platform, ["Boardgame"])
        self.assertEqual(site.resource.item.genre[0], "Economic")
        # self.assertEqual(site.resource.item.other_title[0], "殖民火星")
        self.assertEqual(site.resource.item.designer, ["Jacob Fryxelius"])


class MultiGameSitesTestCase(TestCase):
    databases = "__all__"

    @use_local_response
    def test_games(self):
        url1 = "https://www.igdb.com/games/portal-2"
        url2 = "https://store.steampowered.com/app/620/Portal_2/"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        self.assertEqual(p1.item.id, p2.item.id)
