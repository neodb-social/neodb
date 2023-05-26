from django.test import TestCase
from catalog.common import *
from catalog.models import *
from catalog.music.utils import *


class BasicMusicTest(TestCase):
    def test_gtin(self):
        self.assertIsNone(upc_to_gtin_13("018771208112X"))
        self.assertIsNone(upc_to_gtin_13("999018771208112"))
        self.assertEqual(upc_to_gtin_13("018771208112"), "0018771208112")
        self.assertEqual(upc_to_gtin_13("00042281006722"), "0042281006722")
        self.assertEqual(upc_to_gtin_13("0042281006722"), "0042281006722")


class SpotifyTestCase(TestCase):
    def test_parse(self):
        t_id_type = IdType.Spotify_Album
        t_id_value = "65KwtzkJXw7oT819NFWmEP"
        t_url = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
        site = SiteManager.get_site_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url)
        self.assertEqual(site.id_value, t_id_value)

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
    def test_parse(self):
        t_id_type = IdType.DoubanMusic
        t_id_value = "33551231"
        t_url = "https://music.douban.com/subject/33551231/"
        site = SiteManager.get_site_by_id_type(t_id_type)
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
        self.assertEqual(site.resource.item.other_title, ["橡胶灵魂"])


class MultiMusicSitesTestCase(TestCase):
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
    def test_parse(self):
        t_id_type = IdType.Bandcamp
        t_id_value = "intlanthem.bandcamp.com/album/in-these-times"
        t_url = "https://intlanthem.bandcamp.com/album/in-these-times?from=hpbcw"
        t_url2 = "https://intlanthem.bandcamp.com/album/in-these-times"
        site = SiteManager.get_site_by_id_type(t_id_type)
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
    def test_parse(self):
        t_id_type = IdType.Discogs_Release
        t_id_value = "25829341"
        t_url = "https://www.discogs.com/release/25829341-JID-The-Never-Story"
        t_url_2 = "https://www.discogs.com/release/25829341"
        site = SiteManager.get_site_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
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
    def test_parse(self):
        t_id_type = IdType.Discogs_Master
        t_id_value = "469004"
        t_url = "https://www.discogs.com/master/469004-The-XX-Coexist"
        t_url_2 = "https://www.discogs.com/master/469004"
        site = SiteManager.get_site_by_id_type(t_id_type)
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
    def test_parse(self):
        t_id_type = IdType.AppleMusic
        t_id_value = "892511830"
        t_url = "https://music.apple.com/us/album/%E8%89%B7%E5%85%89%E5%9B%9B%E5%B0%84/892511830"
        t_url_2 = "https://music.apple.com/us/album/892511830"
        site = SiteManager.get_site_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url_2)
        self.assertEqual(site.id_value, t_id_value)

    @use_local_response
    def test_scrape(self):
        t_url = "https://music.apple.com/us/album/%E8%89%B7%E5%85%89%E5%9B%9B%E5%B0%84/892511830"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"].decode("utf-8"), "艷光四射")
        self.assertEqual(site.resource.metadata["artist"], ["HOCC"])
        self.assertIsInstance(site.resource.item, Album)
        self.assertEqual(site.resource.item.genre, ["Cantopop/HK-Pop"])
        self.assertEqual(site.resource.item.duration, 2427103)
