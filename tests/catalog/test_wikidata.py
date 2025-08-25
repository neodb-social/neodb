"""
Tests for the WikiData site implementation
"""

from unittest.mock import patch

import pytest

from catalog.book.models import Work
from catalog.common import ParseError
from catalog.common.downloaders import use_local_response
from catalog.game.models import Game
from catalog.models import IdType
from catalog.movie.models import Movie
from catalog.performance.models import Performance
from catalog.podcast.models import Podcast, PodcastEpisode
from catalog.sites.wikidata import WikiData, WikidataProperties, WikidataTypes
from catalog.tv.models import TVEpisode, TVSeason, TVShow


# Helper functions for testing entity type mapping
def assert_entity_type_mapping(entity_id, entity_type_id, expected_model):
    """Helper function to test Wikidata entity type mapping

    Args:
        entity_id: The Wikidata entity ID (e.g., Q184843)
        entity_type_id: The Wikidata type ID to test (e.g., WikidataTypes.FILM)
        expected_model: The expected NeoDB model class (e.g., Movie)
    """
    # Create mock entity data
    entity_data = {
        "id": entity_id,
        "claims": {
            WikidataProperties.P31: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P31,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": entity_type_id,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    # Initialize WikiData and test
    wiki_site = WikiData(url=f"https://www.wikidata.org/wiki/{entity_id}")
    model = wiki_site._determine_entity_type(entity_data)

    # Assert the expected model
    assert model == expected_model


def assert_entity_with_multiple_types(entity_id, entity_type_ids, expected_model):
    """Helper function to test Wikidata entity with multiple types

    Args:
        entity_id: The Wikidata entity ID (e.g., Q53235)
        entity_type_ids: List of Wikidata type IDs (e.g., [WikidataTypes.TV_EPISODE, WikidataTypes.TV_SPECIAL])
        expected_model: The expected NeoDB model class (e.g., Movie)
    """
    # Create mock entity data with multiple types
    claims = []
    for type_id in entity_type_ids:
        claims.append(
            {
                "mainsnak": {
                    "snaktype": "value",
                    "property": WikidataProperties.P31,
                    "datatype": "wikibase-item",
                    "datavalue": {
                        "value": {
                            "id": type_id,
                            "type": "wikibase-entityid",
                        },
                        "type": "wikibase-entityid",
                    },
                }
            }
        )

    entity_data = {
        "id": entity_id,
        "claims": {WikidataProperties.P31: claims},
    }

    # Initialize WikiData and test
    wiki_site = WikiData(url=f"https://www.wikidata.org/wiki/{entity_id}")
    model = wiki_site._determine_entity_type(entity_data)

    # Assert the expected model
    assert model == expected_model


def create_parent_type_entity(entity_id, instance_type_id, parent_type_id):
    """Create an entity with a direct parent type

    Args:
        entity_id: The entity ID
        instance_type_id: The instance type ID
        parent_type_id: The parent type ID

    Returns:
        Entity data dictionary with both instance and parent types
    """
    return {
        "id": entity_id,
        "claims": {
            WikidataProperties.P31: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P31,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": instance_type_id,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ],
            WikidataProperties.P279: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P279,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": parent_type_id,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ],
        },
    }


def create_v1_api_entity(entity_id, type_ids):
    """Create an entity with v1 API format

    Args:
        entity_id: The entity ID
        type_ids: List of type IDs or single type ID

    Returns:
        Entity data dictionary in v1 API format
    """
    if isinstance(type_ids, str):
        type_ids = [type_ids]

    statements = []
    for type_id in type_ids:
        statements.append({"value": {"id": type_id}})

    return {"id": entity_id, "statements": {WikidataProperties.P31: statements}}


# Group 1: Basic entity type detection tests
def test_basic_entity_type_detection():
    """Test model detection for common entity types using the helper function"""
    # Movie tests
    assert_entity_type_mapping("Q184843", WikidataTypes.FILM, Movie)
    assert_entity_type_mapping("Q226730", WikidataTypes.SILENT_FILM, Movie)
    assert_entity_type_mapping("Q506240", WikidataTypes.TV_FILM, Movie)
    assert_entity_type_mapping("Q220898", WikidataTypes.OVA, Movie)
    assert_entity_type_mapping("Q24862", WikidataTypes.SHORT_FILM, Movie)
    assert_entity_type_mapping("Q18011172", WikidataTypes.FILM_PROJECT, Movie)

    # Book/Work tests
    assert_entity_type_mapping("Q721", WikidataTypes.LITERARY_WORK, Work)
    assert_entity_type_mapping("Q722", WikidataTypes.NOVEL, Work)
    assert_entity_type_mapping("Q45340", WikidataTypes.MEDIA_FRANCHISE, Work)

    # TV show tests
    assert_entity_type_mapping("Q1079", WikidataTypes.TV_SERIES, TVShow)
    assert_entity_type_mapping("Q15416", WikidataTypes.TV_PROGRAM, TVShow)
    assert_entity_type_mapping("Q117467246", WikidataTypes.ANIMATED_TV_SERIES, TVShow)
    assert_entity_type_mapping("Q581714", WikidataTypes.ANIMATED_SERIES, TVShow)
    assert_entity_type_mapping("Q1259759", WikidataTypes.TV_MINISERIES, TVShow)
    assert_entity_type_mapping("Q113687694", WikidataTypes.OVA_SERIES, TVShow)
    assert_entity_type_mapping("Q113671041", WikidataTypes.ONA_SERIES, TVShow)

    # TV seasons and episodes
    assert_entity_type_mapping("Q25361", WikidataTypes.TV_SEASON, TVSeason)
    assert_entity_type_mapping("Q53234", WikidataTypes.TV_EPISODE, TVEpisode)

    # Game test
    assert_entity_type_mapping("Q7889", WikidataTypes.VIDEO_GAME, Game)

    # Podcast tests
    assert_entity_type_mapping("Q24634210", WikidataTypes.PODCAST_SHOW, Podcast)
    assert_entity_type_mapping(
        "Q61855877", WikidataTypes.PODCAST_EPISODE, PodcastEpisode
    )

    # Performance tests
    assert_entity_type_mapping("Q25379", WikidataTypes.PLAY, Performance)
    assert_entity_type_mapping("Q2743", WikidataTypes.MUSICAL, Performance)
    assert_entity_type_mapping("Q1344", WikidataTypes.OPERA, Performance)


# Group 2: Multiple entity type tests
def test_multiple_entity_types():
    """Test entities with multiple types and priority rules"""
    # TV_SPECIAL has priority over TV_EPISODE
    assert_entity_with_multiple_types(
        "Q53235", [WikidataTypes.TV_EPISODE, WikidataTypes.TV_SPECIAL], Movie
    )

    # TV_SERIES should have priority over TV_PROGRAM by first match
    assert_entity_with_multiple_types(
        "Q53236", [WikidataTypes.TV_PROGRAM, WikidataTypes.TV_SERIES], TVShow
    )


# Group 3: Parent type lookup tests
def test_parent_type_lookup():
    """Test model detection using parent type lookup"""
    # Test direct parent type lookup
    entity_data = create_parent_type_entity(
        "Q999999",
        "Q12345",  # Unknown instance type
        WikidataTypes.FILM,  # Known parent type (Film)
    )

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q999999")
    model = wiki_site._determine_entity_type(entity_data)

    # Should identify as Movie from parent type
    assert model == Movie

    # Test instance parent type lookup via API
    entity_with_unknown_type = {
        "id": "Q999998",
        "claims": {
            WikidataProperties.P31: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P31,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": "Q12346",  # Unknown instance type
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
            # No P279 here - will need API lookup
        },
    }

    # Mock the API call to get Q12346's data
    instance_parent_entity = {
        "id": "Q12346",
        "claims": {
            WikidataProperties.P279: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P279,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.TV_SERIES,  # Parent is TV series
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q999998")

    # Mock the API call
    with patch.object(
        wiki_site, "_fetch_entity_by_id", return_value=instance_parent_entity
    ):
        model = wiki_site._determine_entity_type(entity_with_unknown_type)
        # Should identify as TVShow from instance's parent type
        assert model == TVShow


def test_recursive_parent_type_lookup():
    """Test model detection using recursive parent type lookup"""
    # This tests a deeper inheritance hierarchy requiring multiple API calls
    entity_data = {
        "id": "Q999997",
        "claims": {
            WikidataProperties.P31: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P31,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": "Q12347",  # Unknown instance type
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ],
            WikidataProperties.P279: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P279,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": "Q54321",  # Another unknown type
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ],
        },
    }

    # Setup a chain of parent types
    # Q12347 -> Q98765 -> Q54321 -> PODCAST_SHOW
    mock_entity_Q12347 = {
        "id": "Q12347",
        "claims": {
            WikidataProperties.P279: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P279,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": "Q98765",
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    mock_entity_Q98765 = {
        "id": "Q98765",
        "claims": {
            WikidataProperties.P279: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P279,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": "Q54321",
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    mock_entity_Q54321 = {
        "id": "Q54321",
        "claims": {
            WikidataProperties.P279: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P279,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.PODCAST_SHOW,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q999997")

    # Mock the API calls - return different entity data based on the ID requested
    def mock_fetch_entity_by_id(entity_id):
        if entity_id == "Q12347":
            return mock_entity_Q12347
        elif entity_id == "Q98765":
            return mock_entity_Q98765
        elif entity_id == "Q54321":
            return mock_entity_Q54321
        return None

    # Apply the mock
    with patch.object(
        wiki_site, "_fetch_entity_by_id", side_effect=mock_fetch_entity_by_id
    ):
        model = wiki_site._determine_entity_type(entity_data)

        # Should identify as Podcast from recursive parent lookup
        assert model == Podcast


# Group 4: V1 API format tests
def test_v1_api_entity_types():
    """Test extraction of entity types with v1 API format"""
    # Test v1 API format extraction
    entity_data = create_v1_api_entity("Q999999", ["Q12345", "Q67890"])
    entity_data["statements"][WikidataProperties.P279] = [
        {"value": {"id": WikidataTypes.TV_SERIES}}
    ]

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q999999")
    instance_types = wiki_site._extract_entity_types(
        entity_data, WikidataProperties.P31
    )
    parent_types = wiki_site._extract_entity_types(entity_data, WikidataProperties.P279)
    model = wiki_site._determine_entity_type(entity_data)

    assert instance_types == ["Q12345", "Q67890"]
    assert parent_types == [WikidataTypes.TV_SERIES]
    assert model == TVShow  # Should identify as TVShow from parent type


def test_determine_entity_type_v1_api_format():
    """Test model detection with v1 API format"""
    # Test direct v1 API format model detection
    entity_data = create_v1_api_entity("Q184843", WikidataTypes.FILM)

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q184843")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == Movie


# Group 5: Edge case and error tests
def test_edge_cases_and_errors():
    """Test edge cases and error handling"""
    # Test person entity - should raise ParseError
    person_entity = {
        "id": "Q42",
        "claims": {
            WikidataProperties.P31: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P31,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.HUMAN,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q42")
    with pytest.raises(ParseError):
        wiki_site._determine_entity_type(person_entity)

    # Test entity with no instance of properties
    empty_entity = {"id": "Q12345", "claims": {}}
    with pytest.raises(ParseError):
        wiki_site._determine_entity_type(empty_entity)

    # Test unsupported entity type
    unsupported_entity = {
        "id": "Q123456",
        "claims": {
            WikidataProperties.P31: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P31,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {"id": "Q123", "type": "wikibase-entityid"},
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    with pytest.raises(ParseError):
        wiki_site._determine_entity_type(unsupported_entity)


# Group 6: Helper method tests
def test_extract_entity_types():
    """Test extraction of entity types from properties"""
    entity_data = {
        "id": "Q999999",
        "claims": {
            WikidataProperties.P31: [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P31,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": "Q12345",
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                },
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": WikidataProperties.P31,
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": "Q67890",
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                },
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q999999")
    types = wiki_site._extract_entity_types(entity_data, WikidataProperties.P31)

    assert types == ["Q12345", "Q67890"]


def test_preferred_model_in_metadata():
    """Test preferred model is included in metadata"""
    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q184843")
    content = wiki_site.scrape()
    assert "preferred_model" in content.metadata
    assert content.metadata["preferred_model"] == "Movie"


# Group 7: Language handling tests
def test_language_handling():
    """Test language handling in labels and descriptions extraction"""
    # Test preferred labels extraction
    with patch(
        "catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS",
        ["en", "zh", "zh-cn", "zh-tw"],
    ):
        # Mock entity data with labels in multiple languages
        entity_data = {
            "labels": {
                "en": {"value": "Douglas Adams", "language": "en"},
                "zh": {"value": "道格拉斯·亚当斯", "language": "zh"},
                "zh-cn": {"value": "道格拉斯·亚当斯", "language": "zh-cn"},
                "zh-tw": {"value": "道格拉斯·亞當斯", "language": "zh-tw"},
                "de": {"value": "Douglas Adams", "language": "de"},
                "fr": {"value": "Douglas Adams", "language": "fr"},
                "es": {"value": "Douglas Adams", "language": "es"},
                "ja": {"value": "ダグラス・アダムズ", "language": "ja"},
            }
        }

        wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q42")
        labels = wiki_site._extract_labels(entity_data)

        # Verify that only preferred labels are included
        assert "en" in labels
        assert "zh" in labels
        assert "zh-cn" in labels
        assert "zh-tw" in labels
        assert "de" not in labels
        assert "fr" not in labels
        assert labels["en"] == "Douglas Adams"
        assert labels["zh"] == "道格拉斯·亚当斯"
        assert labels["zh-cn"] == "道格拉斯·亚当斯"
        assert labels["zh-tw"] == "道格拉斯·亞當斯"

    # Test preferred descriptions extraction
    with patch(
        "catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS",
        ["en", "zh", "zh-cn", "zh-tw"],
    ):
        # Mock entity data with descriptions in multiple languages
        entity_data = {
            "descriptions": {
                "en": {"value": "English writer and humorist", "language": "en"},
                "zh": {"value": "英国作家", "language": "zh"},
                "zh-cn": {"value": "英国作家", "language": "zh-cn"},
                "zh-tw": {"value": "英國作家", "language": "zh-tw"},
                "de": {"value": "britischer Science-Fiction-Autor", "language": "de"},
                "fr": {"value": "écrivain de science-fiction", "language": "fr"},
            }
        }

        wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q42")
        descriptions = wiki_site._extract_descriptions(entity_data)

        # Verify that only preferred language descriptions are included
        assert len(descriptions) == 4
        assert any(
            d["lang"] == "en" and d["text"] == "English writer and humorist"
            for d in descriptions
        )
        assert any(d["lang"] == "zh" and d["text"] == "英国作家" for d in descriptions)
        assert any(
            d["lang"] == "zh-cn" and d["text"] == "英国作家" for d in descriptions
        )
        assert any(
            d["lang"] == "zh-tw" and d["text"] == "英國作家" for d in descriptions
        )
        assert not any(d["lang"] == "de" for d in descriptions)
        assert not any(d["lang"] == "fr" for d in descriptions)


def test_preferred_languages_expansion():
    """Test language expansion for preferred languages"""
    with patch("catalog.sites.wikidata.SITE_PREFERRED_LANGUAGES", ["en", "zh"]):
        from catalog.sites.wikidata import _get_preferred_languages

        preferred_langs = _get_preferred_languages()
        assert "en" in preferred_langs
        assert "zh" in preferred_langs
        assert "zh-hans" in preferred_langs
        assert "zh-hant" in preferred_langs


class TestWikiData:
    @use_local_response
    def test_url_parsing(self):
        movie_url = "https://www.wikidata.org/wiki/Q83495"  # The Matrix
        site = WikiData(url=movie_url)
        assert site.url == movie_url
        assert site.ID_TYPE == IdType.WikiData
        assert site.id_value == "Q83495"
        alt_url = "https://www.wikidata.org/entity/Q83495"
        site2 = WikiData(url=alt_url)
        assert site2.id_value == "Q83495"

    @patch(
        "catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS",
        ["en", "zh", "zh-hans", "zh-hant"],
    )
    @use_local_response
    def test_scrape_movie(self):
        site = WikiData(url="https://www.wikidata.org/wiki/Q83495")
        content = site.scrape()
        assert content.metadata["title"] == "The Matrix"
        localized_titles = content.metadata["localized_title"]
        assert any(
            t["lang"] == "en" and t["text"] == "The Matrix" for t in localized_titles
        )
        assert any(
            t["lang"] == "zh" and t["text"] == "黑客帝国" for t in localized_titles
        )
        assert any(
            t["lang"] == "zh-hans" and t["text"] == "黑客帝国" for t in localized_titles
        )
        descriptions = content.metadata["localized_description"]
        assert any(
            d["lang"] == "en" and "1999" in d["text"] and "film" in d["text"]
            for d in descriptions
        )
        assert content.metadata["preferred_model"] == "Movie"
        assert "cover_image_url" in content.metadata
        assert (
            content.metadata["cover_image_url"]
            == "https://commons.wikimedia.org/wiki/Special:FilePath/The.Matrix.glmatrix.1.png?width=1000"
        )
        assert content.metadata["release_date"] == "1999-03-31"
        # assert "Q9545711" in content.metadata["director"]  # Lana Wachowski ID
        # assert "Q9544977" in content.metadata["director"]  # Lilly Wachowski ID
        # assert "Q471839" in content.metadata["genre"]  # Science fiction film
        # assert "Q188473" in content.metadata["genre"]  # Action film
        # assert "Q1860" in content.metadata["language"]  # English
        assert content.lookup_ids.get(IdType.IMDB) == "tt0133093"
        assert content.lookup_ids.get(IdType.TMDB_Movie) == "603"
        assert content.lookup_ids.get(IdType.DoubanMovie) == "1291843"

    @use_local_response
    def test_v1_api_format(self):
        site = WikiData(url="https://www.wikidata.org/wiki/Q83495")
        content = site.scrape()
        assert content.metadata["preferred_model"] == "Movie"
        assert (
            content.metadata["cover_image_url"]
            == "https://commons.wikimedia.org/wiki/Special:FilePath/The.Matrix.glmatrix.1.png?width=1000"
        )

    @use_local_response
    def test_scrape_game(self):
        site = WikiData(url="https://www.wikidata.org/wiki/Q3182559")
        content = site.scrape()
        assert content.metadata["title"] == "Cyberpunk 2077"
        assert content.metadata["preferred_model"] == "Game"
        assert content.metadata["release_date"] == "2020-12-10"
        # assert "Q1172164" in content.metadata["developer"]  # CD Projekt Red
        # assert "Q1172164" in content.metadata["publisher"]
        # assert "Q5014725" in content.metadata["platform"]  # Windows
        # assert "Q13361286" in content.metadata["platform"]  # PlayStation 4
        # assert "Q1422746" in content.metadata["genre"]  # Action RPG
        assert content.metadata["official_site"] == "https://www.cyberpunk.net"
        assert content.lookup_ids.get(IdType.Steam) == "1091500"
        assert content.lookup_ids.get(IdType.DoubanGame) == "25931998"

    @use_local_response
    def test_scrape_performance(self):
        site = WikiData(url="https://www.wikidata.org/wiki/Q41567")
        content = site.scrape()
        assert content.metadata["title"] == "Hamlet"
        assert content.metadata["preferred_model"] == "Performance"
        # assert "Q692" in content.metadata["playwright"]  # Shakespeare
        # assert "Q1860" in content.metadata["language"]  # English
        # assert "Q80930" in content.metadata["genre"]  # Tragedy
        assert (
            content.metadata["opening_date"] == "1602-01-01"
        )  # convered from 1602-00-00


def test_extract_openlibrary_ids():
    """Test extraction of OpenLibrary work IDs from WikiData"""
    # Create a WikiData site instance
    site = WikiData(id_value="Q12345")

    # Mock entity data with OpenLibrary work ID (P648)
    entity_data = {"statements": {"P648": [{"value": "OL8694710W"}]}}

    # Extract external IDs
    resources = site._extract_external_ids(entity_data)

    # Find OpenLibrary_Work in the extracted resources
    openlibrary_work = None
    for resource in resources:
        if resource["id_type"] == IdType.OpenLibrary_Work:
            openlibrary_work = resource
            break

    # Verify OpenLibrary work ID was extracted correctly
    assert openlibrary_work is not None
    assert openlibrary_work["id_value"] == "OL8694710W"
    assert openlibrary_work["id_type"] == IdType.OpenLibrary_Work


def test_openlibrary_work_property_mapping():
    """Test that P648 is correctly mapped to OpenLibrary_Work IdType"""
    # Verify the mapping exists in WikidataProperties
    assert "P648" in WikidataProperties.IdTypeMapping
    assert WikidataProperties.IdTypeMapping["P648"] == IdType.OpenLibrary_Work


def test_openlibrary_work_reverse_lookup():
    """Test that we can find the correct Wikidata property for OpenLibrary_Work IdType"""
    from catalog.sites.wikidata import WikidataProperties

    # Find the property ID for OpenLibrary_Work
    property_id = None
    for prop_id, mapped_type in WikidataProperties.IdTypeMapping.items():
        if mapped_type == IdType.OpenLibrary_Work:
            property_id = prop_id
            break

    # Verify we found the correct property
    assert property_id == "P648"

    # This verifies that the lookup_qid_by_external_id method would work correctly
    # for OpenLibrary_Work IDs (though we're not testing the actual API call here)
