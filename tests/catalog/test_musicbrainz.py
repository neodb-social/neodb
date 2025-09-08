import pytest

from catalog.common import *
from catalog.models import Album, IdType, SiteName
from catalog.sites.musicbrainz import MusicBrainzRelease, MusicBrainzReleaseGroup


@pytest.mark.django_db(databases="__all__")
class TestMusicBrainzReleaseGroup:
    def test_parse_release_group_url(self):
        """Test parsing release-group URL"""
        t_id_type = IdType.MusicBrainz_ReleaseGroup
        t_id_value = "b1392450-e666-3926-a536-22c65f834433"
        t_url = f"https://musicbrainz.org/release-group/{t_id_value}"

        # Test site registration
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)

        # Test URL parsing
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert isinstance(site, MusicBrainzReleaseGroup)
        assert site.url == t_url
        assert site.id_value == t_id_value

    def test_parse_release_group_url_with_params(self):
        """Test parsing release-group URL with query parameters"""
        t_id_value = "b1392450-e666-3926-a536-22c65f834433"
        t_url = f"https://musicbrainz.org/release-group/{t_id_value}?tab=releases"

        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert isinstance(site, MusicBrainzReleaseGroup)
        assert site.id_value == t_id_value

    def test_id_to_url(self):
        """Test ID to URL conversion"""
        t_id_value = "b1392450-e666-3926-a536-22c65f834433"
        expected_url = f"https://musicbrainz.org/release-group/{t_id_value}"

        assert MusicBrainzReleaseGroup.id_to_url(t_id_value) == expected_url

    def test_invalid_url_patterns(self):
        """Test that invalid URLs don't match"""
        invalid_urls = [
            "https://musicbrainz.org/release-group/invalid-id",
            "https://musicbrainz.org/release-group/",
            "https://musicbrainz.org/artist/b1392450-e666-3926-a536-22c65f834433",
            "https://example.com/release-group/b1392450-e666-3926-a536-22c65f834433",
        ]

        for url in invalid_urls:
            site = SiteManager.get_site_by_url(url)
            if site is not None:
                assert not isinstance(site, MusicBrainzReleaseGroup)

    # @use_local_response
    def test_scrape_release_group(self):
        """Test scraping release-group data"""
        t_url = (
            "https://musicbrainz.org/release-group/b1392450-e666-3926-a536-22c65f834433"
        )
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert isinstance(site, MusicBrainzReleaseGroup)
        assert not site.ready

        # Test scraping
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None

        # Test metadata
        metadata = site.resource.metadata
        assert metadata["title"] == "OK Computer"
        assert metadata["artist"] == ["Radiohead"]
        assert metadata["release_date"] == "1997-05-21"
        assert len(metadata["genre"]) > 0
        assert "alternative rock" in metadata["genre"]
        assert "track_list" in metadata
        assert "1. Airbag" in metadata["track_list"]
        assert "duration" in metadata
        assert metadata["duration"] > 0
        assert "company" in metadata
        assert "EMI" in metadata["company"]

        # Test localized title
        assert "localized_title" in metadata
        assert len(metadata["localized_title"]) > 0
        assert metadata["localized_title"][0]["text"] == "OK Computer"
        assert metadata["localized_title"][0]["lang"] == "en"

        # Test item creation
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Album)

    def test_extract_track_info(self):
        """Test track information extraction"""
        t_url = (
            "https://musicbrainz.org/release-group/b1392450-e666-3926-a536-22c65f834433"
        )
        site = SiteManager.get_site_by_url(t_url)
        assert isinstance(site, MusicBrainzReleaseGroup)
        release_data = {
            "media": [
                {
                    "position": 1,
                    "tracks": [
                        {"position": 1, "title": "Airbag", "length": 284000},
                        {"position": 2, "title": "Paranoid Android", "length": 383000},
                    ],
                }
            ]
        }
        track_info = site._extract_track_info(release_data)
        assert track_info["track_list"] == "1. Airbag\n2. Paranoid Android"
        assert track_info["duration"] == 667000

    def test_extract_track_info_multi_disc(self):
        """Test track extraction with multiple discs"""
        site = MusicBrainzReleaseGroup()

        release_data = {
            "media": [
                {
                    "position": 1,
                    "tracks": [
                        {"position": 1, "title": "Track 1", "length": 180000},
                    ],
                },
                {
                    "position": 2,
                    "tracks": [
                        {"position": 1, "title": "Track 2", "length": 200000},
                    ],
                },
            ]
        }

        track_info = site._extract_track_info(release_data)
        assert track_info["track_list"] == "1-1. Track 1\n2-1. Track 2"
        assert track_info["duration"] == 380000

    def test_extract_label_info(self):
        """Test label information extraction"""
        site = MusicBrainzReleaseGroup()

        release_data = {
            "label-info": [
                {"label": {"name": "EMI"}},
                {"label": {"name": "Capitol Records"}},
            ]
        }

        labels = site._extract_label_info(release_data)
        assert labels == ["EMI", "Capitol Records"]

    def test_upc_to_gtin_conversion(self):
        """Test UPC to GTIN-13 conversion"""
        site = MusicBrainzReleaseGroup()

        # Test 12-digit UPC conversion
        assert site._upc_to_gtin_13("724385522918") == "0724385522918"

        # Test non-12-digit codes
        assert site._upc_to_gtin_13("1234") == "1234"
        assert site._upc_to_gtin_13("0724385522918") == "0724385522918"


@pytest.mark.django_db(databases="__all__")
class TestMusicBrainzRelease:
    def test_parse_release_url(self):
        """Test parsing release URL"""
        t_id_type = IdType.MusicBrainz_Release
        t_id_value = "1834eae1-741b-3c03-9ca5-0df3decb43ea"
        t_url = f"https://musicbrainz.org/release/{t_id_value}"

        # Test site registration
        site = SiteManager.get_site_cls_by_id_type(t_id_type)
        assert site is not None
        assert site.validate_url(t_url)

        # Test URL parsing
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert isinstance(site, MusicBrainzRelease)
        assert site.url == t_url
        assert site.id_value == t_id_value

    def test_parse_release_url_with_params(self):
        """Test parsing release URL with query parameters"""
        t_id_value = "1834eae1-741b-3c03-9ca5-0df3decb43ea"
        t_url = f"https://musicbrainz.org/release/{t_id_value}?tab=tracklist"

        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert isinstance(site, MusicBrainzRelease)
        assert site.id_value == t_id_value

    def test_id_to_url(self):
        """Test ID to URL conversion"""
        t_id_value = "1834eae1-741b-3c03-9ca5-0df3decb43ea"
        expected_url = f"https://musicbrainz.org/release/{t_id_value}"

        assert MusicBrainzRelease.id_to_url(t_id_value) == expected_url

    def test_invalid_url_patterns(self):
        """Test that invalid URLs don't match"""
        invalid_urls = [
            "https://musicbrainz.org/release/invalid-id",
            "https://musicbrainz.org/release/",
            "https://musicbrainz.org/artist/1834eae1-741b-3c03-9ca5-0df3decb43ea",
            "https://example.com/release/1834eae1-741b-3c03-9ca5-0df3decb43ea",
        ]

        for url in invalid_urls:
            site = SiteManager.get_site_by_url(url)
            if site is not None:
                assert not isinstance(site, MusicBrainzRelease)

    @use_local_response
    def test_scrape_release(self):
        """Test scraping release data"""
        t_url = "https://musicbrainz.org/release/1834eae1-741b-3c03-9ca5-0df3decb43ea"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert isinstance(site, MusicBrainzRelease)
        assert not site.ready

        # Test scraping
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None

        # Test metadata
        metadata = site.resource.metadata
        assert metadata["title"] == "OK Computer"
        assert metadata["artist"] == ["Radiohead"]
        assert metadata["release_date"] == "1997-05-21"
        assert len(metadata["genre"]) > 0
        assert "alternative rock" in metadata["genre"]
        assert "track_list" in metadata
        assert "1. Airbag" in metadata["track_list"]
        assert "duration" in metadata
        assert metadata["duration"] > 0
        assert "company" in metadata
        assert "EMI" in metadata["company"]

        # Test localized title
        assert "localized_title" in metadata
        assert len(metadata["localized_title"]) > 0
        assert metadata["localized_title"][0]["text"] == "OK Computer"
        assert metadata["localized_title"][0]["lang"] == "en"

        # Test item creation
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Album)

    def test_barcode_handling(self):
        """Test barcode to GTIN conversion in release data"""
        site = MusicBrainzRelease()

        # Test with 12-digit UPC
        release_data = {"barcode": "724385522918"}
        result = site._parse_release_data(
            {
                **release_data,
                "title": "Test Album",
                "artist-credit": [{"artist": {"name": "Test Artist"}}],
            }
        )

        assert IdType.GTIN in result.lookup_ids
        assert result.lookup_ids[IdType.GTIN] == "0724385522918"

        # Test with 13-digit EAN
        release_data = {"barcode": "0724385522918"}
        result = site._parse_release_data(
            {
                **release_data,
                "title": "Test Album",
                "artist-credit": [{"artist": {"name": "Test Artist"}}],
            }
        )

        assert IdType.GTIN in result.lookup_ids
        assert result.lookup_ids[IdType.GTIN] == "0724385522918"

    def test_genre_extraction_from_release_and_group(self):
        """Test genre extraction from both release and release-group"""
        site = MusicBrainzRelease()

        release_data = {
            "title": "Test Album",
            "artist-credit": [{"artist": {"name": "Test Artist"}}],
            "genres": [{"name": "rock"}],
            "tags": [{"name": "alternative", "count": 5}],
            "release-group": {
                "genres": [{"name": "indie rock"}],
                "tags": [{"name": "experimental", "count": 3}],
            },
        }

        result = site._parse_release_data(release_data)
        genres = result.metadata["genre"]

        assert "rock" in genres
        assert "alternative" in genres
        assert "indie rock" in genres
        assert "experimental" in genres

    def test_track_extraction_from_release(self):
        """Test track extraction directly from release data"""
        site = MusicBrainzRelease()

        release_data = {
            "media": [
                {
                    "position": 1,
                    "tracks": [
                        {"position": 1, "title": "First Track", "length": 210000},
                        {"position": 2, "title": "Second Track", "length": 180000},
                    ],
                }
            ]
        }

        track_info = site._extract_track_info(release_data)
        assert track_info["track_list"] == "1. First Track\n2. Second Track"
        assert track_info["duration"] == 390000


@pytest.mark.django_db(databases="__all__")
class TestMusicBrainzIntegration:
    def test_both_classes_registered(self):
        """Test that both MusicBrainz classes are properly registered"""
        # Test release-group registration
        rg_site = SiteManager.get_site_cls_by_id_type(IdType.MusicBrainz_ReleaseGroup)
        assert rg_site is not None
        assert rg_site == MusicBrainzReleaseGroup

        # Test release registration
        rel_site = SiteManager.get_site_cls_by_id_type(IdType.MusicBrainz_Release)
        assert rel_site is not None
        assert rel_site == MusicBrainzRelease

    def test_url_routing(self):
        """Test that URLs are routed to the correct class"""
        rg_url = (
            "https://musicbrainz.org/release-group/b1392450-e666-3926-a536-22c65f834433"
        )
        rel_url = "https://musicbrainz.org/release/1834eae1-741b-3c03-9ca5-0df3decb43ea"

        rg_site = SiteManager.get_site_by_url(rg_url)
        assert isinstance(rg_site, MusicBrainzReleaseGroup)

        rel_site = SiteManager.get_site_by_url(rel_url)
        assert isinstance(rel_site, MusicBrainzRelease)

    def test_different_id_types(self):
        """Test that the classes use different ID types"""
        assert MusicBrainzReleaseGroup.ID_TYPE == IdType.MusicBrainz_ReleaseGroup
        assert MusicBrainzRelease.ID_TYPE == IdType.MusicBrainz_Release
        assert MusicBrainzReleaseGroup.ID_TYPE != MusicBrainzRelease.ID_TYPE

    def test_same_site_name(self):
        """Test that both classes share the same site name"""
        assert MusicBrainzReleaseGroup.SITE_NAME == SiteName.MusicBrainz
        assert MusicBrainzRelease.SITE_NAME == SiteName.MusicBrainz
        assert MusicBrainzReleaseGroup.SITE_NAME == MusicBrainzRelease.SITE_NAME

    def test_different_wiki_properties(self):
        """Test that classes have different Wikidata property IDs"""
        assert (
            MusicBrainzReleaseGroup.WIKI_PROPERTY_ID == "P436"
        )  # MusicBrainz release group ID
        assert MusicBrainzRelease.WIKI_PROPERTY_ID == "P437"  # MusicBrainz release ID
        assert (
            MusicBrainzReleaseGroup.WIKI_PROPERTY_ID
            != MusicBrainzRelease.WIKI_PROPERTY_ID
        )

    def test_same_default_model(self):
        """Test that both classes use Album as default model"""
        assert MusicBrainzReleaseGroup.DEFAULT_MODEL == Album
        assert MusicBrainzRelease.DEFAULT_MODEL == Album
