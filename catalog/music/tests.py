from django.test import TestCase

from catalog.common import *
from catalog.models import *
from catalog.music.utils import *


class BasicMusicTest(TestCase):
    databases = "__all__"

    def test_gtin(self):
        self.assertIsNone(upc_to_gtin_13("018771208112X"))
        self.assertIsNone(upc_to_gtin_13("999018771208112"))
        self.assertEqual(upc_to_gtin_13("018771208112"), "0018771208112")
        self.assertEqual(upc_to_gtin_13("00042281006722"), "0042281006722")
        self.assertEqual(upc_to_gtin_13("0042281006722"), "0042281006722")


class SpotifyTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_id_type = IdType.Spotify_Album
        t_id_value = "65KwtzkJXw7oT819NFWmEP"
        t_url = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url)
        self.assertEqual(site.id_value, t_id_value)

        # This errors too often in GitHub actions
        # t_url2 = "https://spotify.link/poyfZyBo6Cb"
        # t_id_value2 = "3yu2aNKeWTxqCjqoIH4HDU"
        # site = SiteManager.get_site_by_url(t_url2)
        # self.assertIsNotNone(site)
        # self.assertEqual(site.id_value, t_id_value2)

    @use_local_response
    def test_scrape_web(self):
        t_url = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
        site = SiteManager.get_site_by_url(t_url)
        r = site.scrape_web()
        self.assertEqual(r.metadata["localized_title"][0]["text"], "The Race For Space")

    @use_local_response
    def test_scrape(self):
        t_url = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "The Race For Space")
        self.assertIsInstance(site.resource.item, Album)
        self.assertEqual(site.resource.item.barcode, "3610159662676")
        self.assertEqual(site.resource.item.genre, [])
        self.assertEqual(site.resource.item.other_title, [])


class DoubanMusicTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_id_type = IdType.DoubanMusic
        t_id_value = "33551231"
        t_url = "https://music.douban.com/subject/33551231/"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url)
        self.assertEqual(site.id_value, t_id_value)

    @use_local_response
    def test_scrape(self):
        t_url = "https://music.douban.com/subject/1401362/"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "Rubber Soul")
        self.assertIsInstance(site.resource.item, Album)
        self.assertEqual(site.resource.item.barcode, "0077774644020")
        self.assertEqual(site.resource.item.genre, ["摇滚"])
        self.assertEqual(len(site.resource.item.localized_title), 2)


class MultiMusicSitesTestCase(TestCase):
    databases = "__all__"

    @use_local_response
    def test_albums(self):
        url1 = "https://music.douban.com/subject/33551231/"
        url2 = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        self.assertEqual(p1.item.id, p2.item.id)

    @use_local_response
    def test_albums_discogs(self):
        url1 = "https://www.discogs.com/release/13574140"
        url2 = "https://open.spotify.com/album/0I8vpSE1bSmysN2PhmHoQg"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        self.assertEqual(p1.item.id, p2.item.id)


class BandcampTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_id_type = IdType.Bandcamp
        t_id_value = "intlanthem.bandcamp.com/album/in-these-times"
        t_url = "https://intlanthem.bandcamp.com/album/in-these-times?from=hpbcw"
        t_url2 = "https://intlanthem.bandcamp.com/album/in-these-times"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url2)
        self.assertEqual(site.id_value, t_id_value)

    @use_local_response
    def test_scrape(self):
        t_url = "https://intlanthem.bandcamp.com/album/in-these-times?from=hpbcw"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "In These Times")
        self.assertEqual(site.resource.metadata["artist"], ["Makaya McCraven"])
        self.assertIsInstance(site.resource.item, Album)
        self.assertEqual(site.resource.item.genre, [])
        self.assertEqual(site.resource.item.other_title, [])


class DiscogsReleaseTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_id_type = IdType.Discogs_Release
        t_id_value = "25829341"
        t_url = "https://www.discogs.com/release/25829341-JID-The-Never-Story"
        t_url_2 = "https://www.discogs.com/release/25829341"
        t_url_3 = "https://www.discogs.com/jp/release/25829341-JID-The-Never-Story"
        t_url_4 = "https://www.discogs.com/pt_BR/release/25829341-JID-The-Never-Story"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url_2)
        site = SiteManager.get_site_by_url(t_url_3)
        self.assertEqual(site.url, t_url_2)
        site = SiteManager.get_site_by_url(t_url_4)
        self.assertEqual(site.url, t_url_2)
        self.assertEqual(site.id_value, t_id_value)
        site = SiteManager.get_site_by_url(t_url_2)
        self.assertIsNotNone(site)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.discogs.com/release/25829341-JID-The-Never-Story"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "The Never Story")
        self.assertEqual(site.resource.metadata["artist"], ["J.I.D"])
        self.assertIsInstance(site.resource.item, Album)
        self.assertEqual(site.resource.item.barcode, "0602445804689")
        self.assertEqual(site.resource.item.genre, ["Hip Hop"])
        self.assertEqual(site.resource.item.other_title, [])


class DiscogsMasterTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_id_type = IdType.Discogs_Master
        t_id_value = "469004"
        t_url = "https://www.discogs.com/master/469004-The-XX-Coexist"
        t_url_2 = "https://www.discogs.com/master/469004"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url_2)
        self.assertEqual(site.id_value, t_id_value)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.discogs.com/master/469004-The-XX-Coexist"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "Coexist")
        self.assertEqual(site.resource.metadata["artist"], ["The XX"])
        self.assertIsInstance(site.resource.item, Album)
        self.assertEqual(site.resource.item.genre, ["Electronic", "Rock", "Pop"])
        self.assertEqual(site.resource.item.other_title, [])


class AppleMusicTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_id_type = IdType.AppleMusic
        t_id_value = "1284391545"
        t_url = "https://music.apple.com/us/album/kids-only/1284391545"
        t_url_2 = "https://music.apple.com/album/1284391545"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url_2)
        self.assertEqual(site.id_value, t_id_value)

    @use_local_response
    def test_scrape(self):
        t_url = "https://music.apple.com/us/album/kids-only/1284391545"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(
            site.resource.metadata["localized_title"][0]["text"], "Kids Only"
        )
        self.assertEqual(site.resource.metadata["artist"], ["Leah Dou"])
        self.assertIsInstance(site.resource.item, Album)
        self.assertEqual(site.resource.item.genre, ["Pop", "Music"])
        self.assertEqual(site.resource.item.duration, 2368000)
