"""
Tests for WikiData model detection functionality
"""

from unittest.mock import MagicMock, patch

import pytest

from catalog.book.models import Work
from catalog.common import ParseError
from catalog.game.models import Game
from catalog.movie.models import Movie
from catalog.performance.models import Performance
from catalog.podcast.models import Podcast, PodcastEpisode
from catalog.sites.wikidata import WikiData, WikidataTypes
from catalog.tv.models import TVEpisode, TVSeason, TVShow


def test_determine_entity_type_movie():
    """Test model detection for a movie entity"""
    # Mock entity data with movie instance of
    entity_data = {
        "id": "Q184843",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.FILM,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q184843")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == Movie


def test_determine_entity_type_book():
    """Test model detection for a book entity"""
    # Mock entity data with book instance of
    entity_data = {
        "id": "Q721",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.LITERARY_WORK,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q721")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == Work


def test_determine_entity_type_novel():
    """Test model detection for a novel entity"""
    # Mock entity data with novel instance of
    entity_data = {
        "id": "Q721",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.NOVEL,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q721")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == Work


def test_determine_entity_type_media_franchise():
    """Test model detection for a media franchise entity"""
    # Mock entity data with media franchise instance of
    entity_data = {
        "id": "Q45340",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.MEDIA_FRANCHISE,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q45340")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == Work


def test_determine_entity_type_tv_series():
    """Test model detection for a TV series entity"""
    # Mock entity data with TV series instance of
    entity_data = {
        "id": "Q1079",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.TV_SERIES,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q1079")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == TVShow


def test_determine_entity_type_tv_season():
    """Test model detection for a TV season entity"""
    # Mock entity data with TV season instance of
    entity_data = {
        "id": "Q25361",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.TV_SEASON,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q25361")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == TVSeason


def test_determine_entity_type_tv_episode():
    """Test model detection for a TV episode entity"""
    # Mock entity data with TV episode instance of
    entity_data = {
        "id": "Q53234",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.TV_EPISODE,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q53234")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == TVEpisode


def test_determine_entity_type_game():
    """Test model detection for a video game entity"""
    # Mock entity data with video game instance of
    entity_data = {
        "id": "Q7889",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.VIDEO_GAME,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q7889")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == Game


def test_determine_entity_type_podcast():
    """Test model detection for a podcast show entity"""
    # Mock entity data with podcast show instance of
    entity_data = {
        "id": "Q24634210",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
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

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q24634210")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == Podcast


def test_determine_entity_type_podcast_episode():
    """Test model detection for a podcast episode entity"""
    # Mock entity data with podcast episode instance of
    entity_data = {
        "id": "Q61855877",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.PODCAST_EPISODE,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q61855877")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == PodcastEpisode


def test_determine_entity_type_play():
    """Test model detection for a theatrical play entity"""
    # Mock entity data with play instance of
    entity_data = {
        "id": "Q25379",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.PLAY,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q25379")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == Performance


def test_determine_entity_type_musical():
    """Test model detection for a musical entity"""
    # Mock entity data with musical instance of
    entity_data = {
        "id": "Q2743",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.MUSICAL,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q2743")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == Performance


def test_determine_entity_type_opera():
    """Test model detection for an opera entity"""
    # Mock entity data with opera instance of
    entity_data = {
        "id": "Q1344",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.OPERA,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q1344")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == Performance


def test_determine_entity_type_person():
    """Test that person entities raise ParseError"""
    # Mock entity data with human instance of
    entity_data = {
        "id": "Q42",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
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
        wiki_site._determine_entity_type(entity_data)


def test_determine_entity_type_no_instance_of():
    """Test that entities with no instance of raise ParseError"""
    # Mock entity data with no instance of properties
    entity_data = {"id": "Q12345", "claims": {}}

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q12345")

    with pytest.raises(ParseError):
        wiki_site._determine_entity_type(entity_data)


def test_determine_entity_type_unsupported():
    """Test that unsupported entity types raise ParseError"""
    # Mock entity data with unsupported instance of
    entity_data = {
        "id": "Q123456",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
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

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q123456")

    with pytest.raises(ParseError):
        wiki_site._determine_entity_type(entity_data)


def test_determine_entity_type_v1_api_format():
    """Test model detection with v1 API format"""
    # Mock entity data with v1 API format
    entity_data = {
        "id": "Q184843",
        "statements": {
            "P31": [
                {
                    "value": {"id": WikidataTypes.FILM},
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q184843")
    model = wiki_site._determine_entity_type(entity_data)

    assert model == Movie


def test_preferred_model_in_metadata():
    """Test that preferred_model is added to metadata"""
    # Mock API entity response for a movie
    mock_entity_response = MagicMock()
    mock_entity_response.status_code = 200
    mock_entity_response.json.return_value = {
        "id": "Q184843",
        "type": "item",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datatype": "wikibase-item",
                        "datavalue": {
                            "value": {
                                "id": WikidataTypes.FILM,
                                "type": "wikibase-entityid",
                            },
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    # Set up mock client with context manager support
    with patch("catalog.sites.wikidata.httpx.Client") as mock_client:
        mock_client_instance = MagicMock()
        mock_client_instance.get.return_value = mock_entity_response
        mock_client_instance.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_instance.__exit__ = MagicMock(return_value=None)
        mock_client.return_value = mock_client_instance

        # Create WikiData site and scrape
        wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q184843")
        content = wiki_site.scrape()

        # Verify preferred_model is set correctly in metadata
        assert "preferred_model" in content.metadata
        assert content.metadata["preferred_model"] == "Movie"
