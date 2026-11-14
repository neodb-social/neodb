import json

import pytest
from lxml import html as lxml_html

from catalog.common import *
from catalog.common.downloaders import (
    RESPONSE_QUOTA_EXCEEDED,
    DownloadError,
    ScraperResponse,
)
from catalog.models import Album
from catalog.models.utils import *
from catalog.sites import bandcamp
from catalog.sites.spotify import Spotify
from catalog.sites.youtube_music import YouTubeMusic


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
        assert site is not None
        assert site.url == t_url
        assert site.id_value == t_id_value

    @use_local_response
    def test_scrape_web(self):
        t_url = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert hasattr(site, "scrape_web")
        assert isinstance(site, Spotify)
        r = site.scrape_web()
        assert r is not None
        assert r.metadata["localized_title"][0]["text"] == "The Race For Space"

    @use_local_response
    def test_scrape(self):
        t_url = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "The Race For Space"
        assert site.resource.item is not None
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
        assert site is not None
        assert site.url == t_url
        assert site.id_value == t_id_value

    @use_local_response
    def test_scrape(self):
        t_url = "https://music.douban.com/subject/1401362/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Rubber Soul"
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Album)
        assert site.resource.item.barcode == "0077774644020"
        assert site.resource.item.genre == ["rock"]
        assert len(site.resource.item.localized_title) == 2


@pytest.mark.django_db(databases="__all__")
class TestMultiMusicSites:
    @use_local_response
    def test_albums(self):
        url1 = "https://music.douban.com/subject/33551231/"
        url2 = "https://open.spotify.com/album/65KwtzkJXw7oT819NFWmEP"
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
        assert isinstance(p1.item, Album)
        assert isinstance(p2.item, Album)
        assert p1.item == p2.item

    @use_local_response
    def test_albums_discogs(self):
        url1 = "https://www.discogs.com/release/13574140"
        url2 = "https://open.spotify.com/album/0I8vpSE1bSmysN2PhmHoQg"
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
        assert isinstance(p1.item, Album)
        assert isinstance(p2.item, Album)
        assert p1.item == p2.item


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
        assert site is not None
        assert site.url == t_url2
        assert site.id_value == t_id_value

    def test_custom_domain_url_to_id(self):
        # Regression: custom Bandcamp domains must resolve
        # via URL_PATTERN_FALLBACK, else scrape() asserts on a None url.
        from catalog.sites.bandcamp import Bandcamp

        custom_url = "https://music.example.com/album/some-album"
        assert Bandcamp.url_to_id(custom_url) == "music.example.com/album/some-album"
        site = Bandcamp(url=custom_url)
        assert site.url == custom_url
        assert site.id_value == "music.example.com/album/some-album"
        # standard *.bandcamp.com URLs still resolve via URL_PATTERNS
        assert (
            Bandcamp.url_to_id("https://intlanthem.bandcamp.com/album/in-these-times")
            == "intlanthem.bandcamp.com/album/in-these-times"
        )

    @use_local_response
    def test_scrape(self):
        t_url = "https://intlanthem.bandcamp.com/album/in-these-times?from=hpbcw"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "In These Times"
        assert site.resource.metadata["artist"] == ["Makaya McCraven"]
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Album)
        assert site.resource.item.genre == []
        meta = site.resource.metadata
        tracks = meta["track_list"].split("\n")
        assert len(tracks) == 11
        assert tracks[0] == "1. In These Times"
        assert tracks[-1] == "11. The Title"
        assert meta["length"] == 2478
        assert meta["release_date"] == "2022-09-23"
        assert meta["bandcamp_album_id"] == 1246560818
        assert meta["cover_image_url"] == "https://f4.bcbits.com/img/a3992958598_10.jpg"
        assert meta["brief"].startswith("In These Times")
        assert "\r" not in meta["brief"]
        lookup = site.resource.other_lookup_ids or {}
        assert lookup.get(IdType.GTIN) == "0075597908541"

    @staticmethod
    def _fake_page(monkeypatch, body: str) -> None:
        class _FakeResponse:
            def html(self):
                return lxml_html.fromstring(f"<html><body>{body}</body></html>")

        class _FakeDownloader:
            def __init__(self, *args, **kwargs):
                pass

            def download(self):
                return _FakeResponse()

        monkeypatch.setattr(bandcamp, "BandcampDownloader", _FakeDownloader)

    def test_scrape_rejects_page_without_title(self, monkeypatch):
        # Regression: query_str now returns "" instead
        # of raising IndexError on a missing element, so the scraper must
        # reject pages lacking title/artist explicitly rather than silently
        # saving empty metadata.
        self._fake_page(monkeypatch, "")
        site = SiteManager.get_site_by_url(
            "https://intlanthem.bandcamp.com/album/in-these-times"
        )
        assert site is not None
        with pytest.raises(ValueError, match="no valid info"):
            site.scrape()

    def test_scrape_falls_back_to_markup(self, monkeypatch):
        # no embedded data, and a malformed block must not break the parse
        self._fake_page(
            monkeypatch,
            """
            <script type="application/ld+json">{not json</script>
            <div id="name-section">
              <h2 class="trackTitle"> Some Album </h2>
              <h3>by <span><a href="https://x.bandcamp.com">Some Artist</a></span></h3>
            </div>
            <div id="tralbumArt"><a href="https://f4.bcbits.com/img/a1_10.jpg"></a></div>
            <div class="tralbumData tralbum-about">About text</div>
            <div class="tralbumData tralbum-credits"> released October 31, 2016 <br></div>
            <meta name="bc-page-properties" content='{"item_type":"a","item_id":42}'>
            """,
        )
        site = SiteManager.get_site_by_url("https://x.bandcamp.com/album/some-album")
        assert site is not None
        pd = site.scrape()
        assert pd.metadata["title"] == "Some Album"
        assert pd.metadata["artist"] == ["Some Artist"]
        assert pd.metadata["release_date"] == "2016-10-31"
        assert pd.metadata["brief"] == "About text"
        assert pd.metadata["cover_image_url"] == "https://f4.bcbits.com/img/a1_10.jpg"
        assert pd.metadata["bandcamp_album_id"] == 42
        assert pd.metadata["track_list"] == ""
        assert pd.metadata["length"] is None
        assert IdType.GTIN not in pd.lookup_ids

    def test_scrape_compilation_tracks(self, monkeypatch):
        tralbum = json.dumps(
            {
                "id": 7,
                "artist": "Various Artists",
                "current": {"title": "Comp", "upc": None},
                "trackinfo": [
                    {"track_num": 1, "title": "One", "artist": "A", "duration": 60.4},
                    {"track_num": 2, "title": "Two", "artist": None, "duration": 30.4},
                    {"track_num": 3, "title": "", "duration": 5},
                ],
            }
        )
        self._fake_page(
            monkeypatch, f"<script data-tralbum='{tralbum}' src='x.js'></script>"
        )
        site = SiteManager.get_site_by_url("https://x.bandcamp.com/album/comp")
        assert site is not None
        pd = site.scrape()
        assert pd.metadata["title"] == "Comp"
        assert pd.metadata["artist"] == ["Various Artists"]
        assert pd.metadata["track_list"] == "1. A - One\n2. Two"
        assert pd.metadata["length"] == 91
        assert pd.metadata["bandcamp_album_id"] == 7


_BANDCAMP_ALBUM = b'<html><div id="name-section"></div></html>'
_BANDCAMP_CHALLENGE = b"<html><title>Client Challenge</title></html>"


class TestBandcampDownloader:
    URL = "https://x.bandcamp.com/album/some-album"

    @staticmethod
    def _setup(
        monkeypatch,
        direct: tuple[bytes, int],
        scrapers: dict[str, bytes | None],
        providers: list[str],
        custom: bool,
    ) -> list[str]:
        calls: list[str] = []

        def _download(self, url):
            calls.append("direct")
            resp = ScraperResponse(url, direct[0], status_code=direct[1])
            return resp, self.validate_response(resp)

        def _scrape_with_provider(self, provider):
            calls.append(provider)
            content = scrapers.get(provider)
            if content is None:
                return None, RESPONSE_QUOTA_EXCEEDED
            return ScraperResponse(self.url, content), RESPONSE_OK

        cls = bandcamp.BandcampDownloader
        monkeypatch.setattr(cls, "_download", _download)
        monkeypatch.setattr(cls, "_scrape_with_provider", _scrape_with_provider)
        monkeypatch.setattr(cls, "get_providers", lambda self: list(providers))
        monkeypatch.setattr(
            cls, "get_backup_provider", lambda self: "custom" if custom else None
        )
        return calls

    def test_direct_page_skips_scrapers(self, monkeypatch):
        calls = self._setup(
            monkeypatch, (_BANDCAMP_ALBUM, 200), {}, ["decodo"], custom=True
        )
        resp = bandcamp.BandcampDownloader(self.URL).download()
        assert resp.content == _BANDCAMP_ALBUM
        assert calls == ["direct"]

    def test_challenge_tries_custom_before_providers(self, monkeypatch):
        calls = self._setup(
            monkeypatch,
            (_BANDCAMP_CHALLENGE, 200),
            {
                "custom": _BANDCAMP_CHALLENGE,
                "decodo": None,
                "scrapfly": _BANDCAMP_ALBUM,
            },
            ["decodo", "scrapfly", "scraperapi"],
            custom=True,
        )
        resp = bandcamp.BandcampDownloader(self.URL).download()
        assert resp.content == _BANDCAMP_ALBUM
        assert calls == ["direct", "custom", "decodo", "scrapfly"]

    def test_custom_scraper_alone_is_used(self, monkeypatch):
        calls = self._setup(
            monkeypatch,
            (_BANDCAMP_CHALLENGE, 200),
            {"custom": _BANDCAMP_ALBUM},
            [],
            custom=True,
        )
        resp = bandcamp.BandcampDownloader(self.URL).download()
        assert resp.content == _BANDCAMP_ALBUM
        assert calls == ["direct", "custom"]

    def test_challenge_everywhere_raises_clear_error(self, monkeypatch):
        calls = self._setup(
            monkeypatch,
            (_BANDCAMP_CHALLENGE, 200),
            {"decodo": _BANDCAMP_CHALLENGE},
            ["decodo"],
            custom=False,
        )
        with pytest.raises(DownloadError, match="bot challenge") as e:
            bandcamp.BandcampDownloader(self.URL).download()
        assert e.value.response_type == RESPONSE_NETWORK_ERROR
        assert calls == ["direct", "decodo"]

    def test_missing_album_skips_scrapers(self, monkeypatch):
        calls = self._setup(monkeypatch, (b"gone", 404), {}, ["decodo"], custom=True)
        with pytest.raises(DownloadError) as e:
            bandcamp.BandcampDownloader(self.URL).download()
        assert e.value.response_type == RESPONSE_INVALID_CONTENT
        assert "bot challenge" not in e.value.message
        assert calls == ["direct"]


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
        assert site is not None
        assert site.url == t_url_2
        site = SiteManager.get_site_by_url(t_url_3)
        assert site is not None
        assert site.url == t_url_2
        site = SiteManager.get_site_by_url(t_url_4)
        assert site is not None
        assert site.url == t_url_2
        assert site.id_value == t_id_value
        site = SiteManager.get_site_by_url(t_url_2)
        assert site is not None

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.discogs.com/release/25829341-JID-The-Never-Story"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "The Never Story"
        assert site.resource.metadata["artist"] == ["J.I.D"]
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Album)
        assert site.resource.item.barcode == "0602445804689"
        assert site.resource.item.genre == ["hip-hop"]


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
        assert site is not None
        assert site.url == t_url_2
        assert site.id_value == t_id_value

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.discogs.com/master/469004-The-XX-Coexist"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Coexist"
        assert site.resource.metadata["artist"] == ["The XX"]
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Album)
        assert site.resource.item.genre == ["electronic", "rock", "pop"]


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
        assert site is not None
        assert site.url == t_url_2
        assert site.id_value == t_id_value

    @use_local_response
    def test_scrape(self):
        t_url = "https://music.apple.com/us/album/kids-only/1284391545"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["localized_title"][0]["text"] == "Kids Only"
        assert site.resource.metadata["artist"] == ["Leah Dou"]
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Album)
        assert site.resource.item.genre == ["pop", "music"]
        assert site.resource.item.length == 2368

    @use_local_response
    def test_scrape_track_without_duration(self):
        t_url = "https://music.apple.com/us/album/flow-motion-remastered/711472863"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        meta = site.resource.metadata
        assert meta["localized_title"][0]["text"] == "Flow Motion (Remastered)"
        assert meta["artist"] == ["Faust"]
        assert meta["track_list"] == "Casablanca Moon\nFlow Motion\nRun"
        assert meta["length"] == 420


@pytest.mark.django_db(databases="__all__")
class TestYouTubeMusic:
    # Real data: Synchronicity (Remastered 2003) by The Police
    t_id = "OLAK5uy_mYp2X6OH03hsFOLZHrnMk7griAOuA9kuA"
    t_url = f"https://music.youtube.com/playlist?list={t_id}"

    def test_parse(self):
        site = SiteManager.get_site_cls_by_id_type(IdType.YouTubeMusic)
        assert site is not None
        assert site is YouTubeMusic
        assert site.validate_url(self.t_url)
        site = SiteManager.get_site_by_url(self.t_url)
        assert site is not None
        assert site.url == self.t_url
        assert site.id_value == self.t_id

    @use_local_response
    def test_scrape(self):
        site = SiteManager.get_site_by_url(self.t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.metadata["title"] == "Synchronicity (Remastered 2003)"
        assert site.resource.metadata["artist"] == ["The Police"]
        assert site.resource.metadata["release_date"] == "1983"
        assert site.resource.metadata["album_type"] == ["album"]
        assert site.resource.metadata["length"] == 2675
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Album)
        assert site.resource.item.release_date == "1983"


@pytest.mark.django_db(databases="__all__")
class TestRateYourMusic:
    def test_parse(self):
        t_id_type = IdType.RateYourMusic_Release
        t_id_value = "release/album/radiohead/ok-computer"
        t_url = "https://rateyourmusic.com/release/album/radiohead/ok-computer/"
        t_url_no_slash = "https://rateyourmusic.com/release/album/radiohead/ok-computer"
        t_url_with_www = (
            "https://www.rateyourmusic.com/release/album/radiohead/ok-computer/"
        )
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)
        assert site.validate_url(t_url_no_slash)
        assert site.validate_url(t_url_with_www)
        s = SiteManager.get_site_by_url(t_url)
        assert s is not None
        assert s.url == t_url
        assert s.id_value == t_id_value
        # www variant normalizes to canonical URL
        s2 = SiteManager.get_site_by_url(t_url_with_www)
        assert s2 is not None
        assert s2.url == t_url
        assert s2.id_value == t_id_value
        # Non-album release types are accepted by URL pattern
        ep_url = "https://rateyourmusic.com/release/ep/some-artist/some-ep/"
        assert site.validate_url(ep_url)

    @use_local_response
    def test_scrape(self):
        t_url = "https://rateyourmusic.com/release/album/radiohead/ok-computer/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert not site.ready
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        meta = site.resource.metadata
        assert meta["title"] == "OK Computer"
        assert meta["artist"] == ["Radiohead"]
        assert meta["album_type"] == ["album"]
        assert meta["release_date"] == "1997-06-16"
        # Primary + secondary genres preserved in order
        assert meta["genre"][:2] == ["Alternative Rock", "Art Rock"]
        assert "Post-Britpop" in meta["genre"]
        assert "Space Rock Revival" in meta["genre"]
        # Tracks: 12 songs, ~53:21 total
        track_lines = meta["track_list"].splitlines()
        assert len(track_lines) == 12
        assert track_lines[0] == "1. Airbag (4:44)"
        assert track_lines[-1] == "12. The Tourist (5:24)"
        assert meta["length"] == 3201
        # Primary label parsed out of og:description
        assert meta["company"] == ["Parlophone"]
        assert meta["cover_image_url"].startswith("https://cdn.sonemic.net/")
        # Brief excludes the "Featured performers:" credit dump
        assert "Featured pe" not in meta["brief"]
        assert meta["localized_title"][0]["text"] == "OK Computer"
        # Streaming-platform IDs harvested from data-links blob
        lookup = site.resource.other_lookup_ids or {}
        assert lookup.get(IdType.Spotify_Album) == "7dxKtc08dYeRVHt3p9CZJn"
        assert lookup.get(IdType.AppleMusic) == "1097861387"
        # No bandcamp on this release (major-label, not self-published)
        assert IdType.Bandcamp not in lookup
        # Item creation
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Album)
        assert str(site.resource.item.release_date) == "1997-06-16"

    @use_local_response
    def test_scrape_bandcamp_release(self):
        # King Gizzard self-publishes on Bandcamp; data-links has bandcamp
        # (with a `url` field) and youtube, but no spotify.
        t_url = (
            "https://rateyourmusic.com/release/album/"
            "king-gizzard-and-the-lizard-wizard/12-bar-bruise/"
        )
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        meta = site.resource.metadata
        assert meta["title"] == "12 Bar Bruise"
        assert meta["artist"] == ["King Gizzard & The Lizard Wizard"]
        lookup = site.resource.other_lookup_ids or {}
        # Bandcamp url field, not RYM's internal numeric id
        assert (
            lookup.get(IdType.Bandcamp)
            == "kinggizzard.bandcamp.com/album/12-bar-bruise"
        )
        # Apple Music: RYM marks the de-locale entry as default
        assert lookup.get(IdType.AppleMusic) == "1649045961"
        # Spotify absent for this release in RYM's data-links
        assert IdType.Spotify_Album not in lookup
