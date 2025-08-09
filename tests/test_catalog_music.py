import pytest

from catalog.common import *
from catalog.models import *
from catalog.music.utils import *


@pytest.mark.django_db(databases="__all__")
class TestBasicMusic:
    def test_gtin(self):
        assert upc_to_gtin_13("018771208112X") is None
        assert upc_to_gtin_13("999018771208112") is None
        assert upc_to_gtin_13("018771208112") == "0018771208112"
        assert upc_to_gtin_13("00042281006722") == "0042281006722"
        assert upc_to_gtin_13("0042281006722") == "0042281006722"


@pytest.mark.django_db(databases="__all__")
class TestSpotify:
    def test_parse(self):
        t_id_type = IdType.Spotify_Album
        t_id_value = "65KwtzkJXw7oT819NFWmEP"
        t_url = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site.url == t_url
        assert site.id_value == t_id_value

        # This errors too often in GitHub actions
        # t_url2 = "https://spotify.link/poyfZyBo6Cb"
        # t_id_value2 = "3yu2aNKeWTxqCjqoIH4HDU"
        # site = SiteManager.get_site_by_url(t_url2)
        # assert site is not None
        # assert site.id_value == t_id_value2

    @use_local_response
    def test_scrape_web(self):
        t_url = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
        site = SiteManager.get_site_by_url(t_url)
        r = site.scrape_web()
        assert r.metadata["localized_title"][0]["text"] == "The Race For Space"

    @use_local_response
    def test_scrape(self):
        t_url = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
        site = SiteManager.get_site_by_url(t_url)
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource.metadata["title"] == "The Race For Space"
        assert isinstance(site.resource.item, Album)
        assert site.resource.item.barcode == "3610159662676"
        assert site.resource.item.genre == []


@pytest.mark.django_db(databases="__all__")
class TestDoubanMusic:
    def test_parse(self):
        t_id_type = IdType.DoubanMusic
        t_id_value = "33551231"
        t_url = "https://music.douban.com/subject/33551231/"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site.url == t_url
        assert site.id_value == t_id_value

    @use_local_response
    def test_scrape(self):
        t_url = "https://music.douban.com/subject/1401362/"
        site = SiteManager.get_site_by_url(t_url)
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource.metadata["title"] == "Rubber Soul"
        assert isinstance(site.resource.item, Album)
        assert site.resource.item.barcode == "0077774644020"
        assert site.resource.item.genre == ["摇滚"]
        assert len(site.resource.item.localized_title) == 2


@pytest.mark.django_db(databases="__all__")
class TestMultiMusicSites:
    @use_local_response
    def test_albums(self):
        url1 = "https://music.douban.com/subject/33551231/"
        url2 = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        assert p1.item.id == p2.item.id

    @use_local_response
    def test_albums_discogs(self):
        url1 = "https://www.discogs.com/release/13574140"
        url2 = "https://open.spotify.com/album/0I8vpSE1bSmysN2PhmHoQg"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        assert p1.item.id == p2.item.id


@pytest.mark.django_db(databases="__all__")
class TestBandcamp:
    def test_parse(self):
        t_id_type = IdType.Bandcamp
        t_id_value = "intlanthem.bandcamp.com/album/in-these-times"
        t_url = "https://intlanthem.bandcamp.com/album/in-these-times?from=hpbcw"
        t_url2 = "https://intlanthem.bandcamp.com/album/in-these-times"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site.url == t_url2
        assert site.id_value == t_id_value

    @use_local_response
    def test_scrape(self):
        t_url = "https://intlanthem.bandcamp.com/album/in-these-times?from=hpbcw"
        site = SiteManager.get_site_by_url(t_url)
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource.metadata["title"] == "In These Times"
        assert site.resource.metadata["artist"] == ["Makaya McCraven"]
        assert isinstance(site.resource.item, Album)
        assert site.resource.item.genre == []


@pytest.mark.django_db(databases="__all__")
class TestDiscogsRelease:
    def test_parse(self):
        t_id_type = IdType.Discogs_Release
        t_id_value = "25829341"
        t_url = "https://www.discogs.com/release/25829341-JID-The-Never-Story"
        t_url_2 = "https://www.discogs.com/release/25829341"
        t_url_3 = "https://www.discogs.com/jp/release/25829341-JID-The-Never-Story"
        t_url_4 = "https://www.discogs.com/pt_BR/release/25829341-JID-The-Never-Story"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site.url == t_url_2
        site = SiteManager.get_site_by_url(t_url_3)
        assert site.url == t_url_2
        site = SiteManager.get_site_by_url(t_url_4)
        assert site.url == t_url_2
        assert site.id_value == t_id_value
        site = SiteManager.get_site_by_url(t_url_2)
        assert site is not None

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.discogs.com/release/25829341-JID-The-Never-Story"
        site = SiteManager.get_site_by_url(t_url)
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource.metadata["title"] == "The Never Story"
        assert site.resource.metadata["artist"] == ["J.I.D"]
        assert isinstance(site.resource.item, Album)
        assert site.resource.item.barcode == "0602445804689"
        assert site.resource.item.genre == ["Hip Hop"]


@pytest.mark.django_db(databases="__all__")
class TestDiscogsMaster:
    def test_parse(self):
        t_id_type = IdType.Discogs_Master
        t_id_value = "469004"
        t_url = "https://www.discogs.com/master/469004-The-XX-Coexist"
        t_url_2 = "https://www.discogs.com/master/469004"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site.url == t_url_2
        assert site.id_value == t_id_value

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.discogs.com/master/469004-The-XX-Coexist"
        site = SiteManager.get_site_by_url(t_url)
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource.metadata["title"] == "Coexist"
        assert site.resource.metadata["artist"] == ["The XX"]
        assert isinstance(site.resource.item, Album)
        assert site.resource.item.genre == ["Electronic", "Rock", "Pop"]


@pytest.mark.django_db(databases="__all__")
class TestAppleMusic:
    def test_parse(self):
        t_id_type = IdType.AppleMusic
        t_id_value = "1284391545"
        t_url = "https://music.apple.com/us/album/kids-only/1284391545"
        t_url_2 = "https://music.apple.com/album/1284391545"
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)
        site = SiteManager.get_site_by_url(t_url)
        assert site.url == t_url_2
        assert site.id_value == t_id_value

    @use_local_response
    def test_scrape(self):
        t_url = "https://music.apple.com/us/album/kids-only/1284391545"
        site = SiteManager.get_site_by_url(t_url)
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource.metadata["localized_title"][0]["text"] == "Kids Only"
        assert site.resource.metadata["artist"] == ["Leah Dou"]
        assert isinstance(site.resource.item, Album)
        assert site.resource.item.genre == ["Pop", "Music"]
        assert site.resource.item.duration == 2368000
