from django.test import TestCase
from catalog.common import *
from catalog.models import *


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
        t_url = "https://music.douban.com/subject/33551231/"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "The Race For Space")
        self.assertIsInstance(site.resource.item, Album)
        self.assertEqual(site.resource.item.barcode, "3610159662676")


class MultiMusicSitesTestCase(TestCase):
    @use_local_response
    def test_albums(self):
        url1 = "https://music.douban.com/subject/33551231/"
        url2 = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
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


class DiscogsReleaseTestCase(TestCase):
    def test_parse(self):
        t_id_type = IdType.Discogs_Release
        t_id_value = "25746742"
        t_url = (
            "https://www.discogs.com/release/25746742-Phish-LP-on-LP-04-Ghost-5222000"
        )
        t_url_2 = "https://www.discogs.com/release/25746742"
        site = SiteManager.get_site_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url_2)
        self.assertEqual(site.id_value, t_id_value)

    @use_local_response
    def test_scrape(self):
        t_url = (
            "https://www.discogs.com/release/25746742-Phish-LP-on-LP-04-Ghost-5222000"
        )
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(
            site.resource.metadata["title"], 'LP on LP 04: "Ghost" 5/22/2000'
        )
        self.assertEqual(site.resource.metadata["artist"], ["Phish"])
        self.assertIsInstance(site.resource.item, Album)
        self.assertEqual(site.resource.item.barcode, "850014859275")


class DiscogsMasterTestCase(TestCase):
    def test_parse(self):
        t_id_type = IdType.Discogs_Master
        t_id_value = "14772"
        t_url = "https://www.discogs.com/master/14772-Linda-Ronstadt-Silk-Purse"
        t_url_2 = "https://www.discogs.com/master/14772"
        site = SiteManager.get_site_by_id_type(t_id_type)
        self.assertIsNotNone(site)
        self.assertEqual(site.validate_url(t_url), True)
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.url, t_url_2)
        self.assertEqual(site.id_value, t_id_value)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.discogs.com/master/14772-Linda-Ronstadt-Silk-Purse"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata["title"], "Silk Purse")
        self.assertEqual(site.resource.metadata["artist"], ["Linda Ronstadt"])
        self.assertIsInstance(site.resource.item, Album)
