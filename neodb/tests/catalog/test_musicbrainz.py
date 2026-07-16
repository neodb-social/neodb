import pytest

from catalog.common import *
from catalog.models import Album, IdType, People, PeopleType
from catalog.sites.musicbrainz import (
    MusicBrainzArtist,
    MusicBrainzRelease,
    MusicBrainzReleaseGroup,
    _extract_first_isrc,
    _extract_label_info,
    _extract_track_info,
)


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
            site = SiteManager.get_site_by_url(
                url, detect_redirection=False, detect_fallback=False
            )
            if site is not None:
                assert not isinstance(site, MusicBrainzReleaseGroup)

    @use_local_response
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
        assert "length" in metadata
        assert metadata["length"] > 0
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

        # Related artist link is emitted so the auto-fetch pipeline can pull
        # the MusicBrainz artist resource for Radiohead.
        related = metadata.get("related_resources") or []
        assert any(
            r.get("model") == "People"
            and r.get("id_type") == IdType.MusicBrainz_Artist
            and r.get("id_value") == "a74b1b7f-71a5-4011-9441-d0b5e4122711"
            for r in related
        )

        # ISRC is harvested from the first track's recording.isrcs.
        assert site.resource.other_lookup_ids.get(IdType.ISRC) == "GBAYE9700001"

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
        track_info = _extract_track_info(release_data)
        assert track_info["track_list"] == "1. Airbag\n2. Paranoid Android"
        assert track_info["duration"] == 667

    def test_extract_track_info_multi_disc(self):
        """Test track extraction with multiple discs"""

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

        track_info = _extract_track_info(release_data)
        assert track_info["track_list"] == "1-1. Track 1\n2-1. Track 2"
        assert track_info["duration"] == 380

    def test_extract_track_info_null_length(self):
        """MB returns "length": null for tracks of unknown length; the key is
        present but the value is None, which int() can't take (EGGPLANT-1E4)."""
        release_data = {
            "media": [
                {
                    "position": 1,
                    "tracks": [
                        {"position": 1, "title": "Known", "length": 284000},
                        {"position": 2, "title": "Unknown", "length": None},
                        {"position": 3, "title": "Missing"},
                    ],
                }
            ]
        }
        track_info = _extract_track_info(release_data)
        assert track_info["track_list"] == "1. Known\n2. Unknown\n3. Missing"
        assert track_info["duration"] == 284

    def test_extract_label_info(self):
        """Test label information extraction"""

        release_data = {
            "label-info": [
                {"label": {"name": "EMI"}},
                {"label": {"name": "Capitol Records"}},
            ]
        }

        labels = _extract_label_info(release_data)
        assert labels == ["EMI", "Capitol Records"]

    def test_upc_to_gtin_conversion(self):
        """Test UPC to GTIN-13 conversion"""
        site = MusicBrainzReleaseGroup()

        # Test 12-digit UPC conversion
        assert site._upc_to_gtin_13("724385522918") == "0724385522918"

        # Test non-12-digit codes
        assert site._upc_to_gtin_13("1234") == "1234"
        assert site._upc_to_gtin_13("0724385522918") == "0724385522918"

    def test_release_group_accepts_ean13_barcode(self):
        """Release-group used to only accept 12-digit UPC; 13-digit EAN now
        flows through unchanged."""
        site = MusicBrainzReleaseGroup(id_value="00000000-0000-0000-0000-000000000000")
        pd = site._parse_release_group_data(
            {
                "title": "Test Album",
                "artist-credit": [{"artist": {"name": "Test Artist"}}],
                "releases": [
                    {"id": "release-id", "barcode": "0724385522918"},
                ],
            }
        )
        # No network for release details was attempted (release_id present but
        # _get_release_details would 404 in tests — wrapped in try/except, so
        # we only care that GTIN was lifted from the release-level barcode).
        assert pd.lookup_ids[IdType.GTIN] == "0724385522918"


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
            site = SiteManager.get_site_by_url(
                url, detect_redirection=False, detect_fallback=False
            )
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
        assert "length" in metadata
        assert metadata["length"] > 0
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

        related = metadata.get("related_resources") or []
        assert any(
            r.get("model") == "People"
            and r.get("id_type") == IdType.MusicBrainz_Artist
            and r.get("id_value") == "a74b1b7f-71a5-4011-9441-d0b5e4122711"
            for r in related
        )

        assert site.resource.other_lookup_ids.get(IdType.ISRC) == "GBAYE9700001"

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

        track_info = _extract_track_info(release_data)
        assert track_info["track_list"] == "1. First Track\n2. Second Track"
        assert track_info["duration"] == 390

    def test_track_extraction_null_length(self):
        """A release whose tracks carry "length": null must not crash on
        int(None) while summing duration (EGGPLANT-1E4)."""
        release_data = {
            "media": [
                {
                    "position": 1,
                    "tracks": [
                        {"position": 1, "title": "First Track", "length": 210000},
                        {"position": 2, "title": "Second Track", "length": None},
                    ],
                }
            ]
        }
        track_info = _extract_track_info(release_data)
        assert track_info["track_list"] == "1. First Track\n2. Second Track"
        assert track_info["duration"] == 210


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

    def test_extract_first_isrc(self):
        """_extract_first_isrc returns the first non-empty ISRC across media."""
        data = {
            "media": [
                {"tracks": [{"recording": {}}]},
                {
                    "tracks": [
                        {"recording": {"isrcs": []}},
                        {"recording": {"isrcs": ["GBAYE9700001", "USRC17600002"]}},
                    ]
                },
            ]
        }
        assert _extract_first_isrc(data) == "GBAYE9700001"

    def test_extract_first_isrc_returns_none_when_absent(self):
        assert _extract_first_isrc({}) is None
        assert _extract_first_isrc({"media": [{"tracks": []}]}) is None


@pytest.mark.django_db(databases="__all__")
class TestMusicBrainzArtist:
    ARTIST_ID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
    ARTIST_URL = f"https://musicbrainz.org/artist/{ARTIST_ID}"

    def test_parse_artist_url(self):
        site = SiteManager.get_site_cls_by_id_type(IdType.MusicBrainz_Artist)
        assert site is MusicBrainzArtist
        assert site.validate_url(self.ARTIST_URL)

        site = SiteManager.get_site_by_url(self.ARTIST_URL)
        assert isinstance(site, MusicBrainzArtist)
        assert site.url == self.ARTIST_URL
        assert site.id_value == self.ARTIST_ID

    def test_id_to_url(self):
        assert MusicBrainzArtist.id_to_url(self.ARTIST_ID) == self.ARTIST_URL

    def test_invalid_url_patterns(self):
        invalid_urls = [
            "https://musicbrainz.org/artist/invalid-id",
            "https://musicbrainz.org/artist/",
            f"https://musicbrainz.org/release/{self.ARTIST_ID}",
            f"https://example.com/artist/{self.ARTIST_ID}",
        ]
        for url in invalid_urls:
            site = SiteManager.get_site_by_url(
                url, detect_redirection=False, detect_fallback=False
            )
            if site is not None:
                assert not isinstance(site, MusicBrainzArtist)

    def test_parse_artist_data_group(self):
        site = MusicBrainzArtist(id_value=self.ARTIST_ID)
        data = {
            "id": self.ARTIST_ID,
            "name": "Radiohead",
            "sort-name": "Radiohead",
            "type": "Group",
            "disambiguation": "British rock band from Abingdon",
            "life-span": {"begin": "1985", "end": None, "ended": False},
            "aliases": [
                {"name": "Radiohead", "locale": "en", "type": "Artist name"},
                {"name": "レディオヘッド", "locale": "ja", "type": "Artist name"},
                # Non-display alias types and locale-less aliases must be
                # ignored to keep localized_name a display-only field.
                {"name": "Radiohead, The", "locale": "en", "type": "Sort name"},
                {"name": "On a Friday", "locale": None, "type": "Artist name"},
            ],
            "relations": [
                {
                    "type": "official homepage",
                    "url": {"resource": "https://www.radiohead.com/"},
                },
                {
                    "type": "wikidata",
                    "url": {"resource": "https://www.wikidata.org/wiki/Q7444"},
                },
            ],
        }
        pd = site._parse_artist_data(data)
        meta = pd.metadata
        assert meta["title"] == "Radiohead"
        assert meta["people_type"] == PeopleType.ORGANIZATION.value
        assert meta["birth_date"] == "1985"
        assert meta["death_date"] is None
        assert meta["official_site"] == "https://www.radiohead.com/"
        names = {(n["lang"], n["text"]) for n in meta["localized_name"]}
        assert ("en", "Radiohead") in names
        assert ("ja", "レディオヘッド") in names
        # Sort-name and locale-less aliases are dropped.
        assert all(text != "Radiohead, The" for _, text in names)
        assert all(text != "On a Friday" for _, text in names)
        assert pd.lookup_ids.get(IdType.WikiData) == "Q7444"

    def test_qid_extraction_only_from_wikidata_host(self):
        site = MusicBrainzArtist(id_value=self.ARTIST_ID)
        pd = site._parse_artist_data(
            {
                "id": self.ARTIST_ID,
                "name": "Radiohead",
                "relations": [
                    # First, a non-wikidata URL that happens to contain "/Q1".
                    {
                        "type": "wikidata",
                        "url": {
                            "resource": "https://example.org/Q1/redirect?to=wikidata"
                        },
                    },
                ],
            }
        )
        assert IdType.WikiData not in pd.lookup_ids

    def test_parse_artist_data_person(self):
        site = MusicBrainzArtist(id_value="00000000-0000-0000-0000-000000000001")
        pd = site._parse_artist_data(
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "name": "Thom Yorke",
                "type": "Person",
                "life-span": {"begin": "1968-10-07"},
            }
        )
        assert pd.metadata["people_type"] == PeopleType.PERSON.value
        assert pd.metadata["birth_date"] == "1968-10-07"

    def test_missing_name_raises(self):
        site = MusicBrainzArtist(id_value=self.ARTIST_ID)
        with pytest.raises(ParseError):
            site._parse_artist_data({"id": self.ARTIST_ID, "name": ""})

    @use_local_response
    def test_scrape_artist(self):
        site = SiteManager.get_site_by_url(self.ARTIST_URL)
        assert isinstance(site, MusicBrainzArtist)
        assert not site.ready

        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        assert site.resource.item is not None
        assert isinstance(site.resource.item, People)
        assert site.resource.item.is_organization
        assert site.resource.item.display_name == "Radiohead"
        # Wikidata QID is picked up from MusicBrainz url-rels.
        assert site.resource.other_lookup_ids.get(IdType.WikiData) == "Q7444"
