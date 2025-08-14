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
    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q184843")
    content = wiki_site.scrape()
    assert "preferred_model" in content.metadata
    assert content.metadata["preferred_model"] == "Movie"


@patch(
    "catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS",
    ["en", "zh", "zh-cn", "zh-tw"],
)
def test_extract_labels_preferred_only():
    """Test that _extract_labels only includes labels in preferred languages"""
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


@patch(
    "catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS",
    ["en", "zh", "zh-cn", "zh-tw"],
)
def test_extract_descriptions_preferred_only():
    """Test that _extract_descriptions only includes descriptions in preferred languages"""
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
    assert any(d["lang"] == "zh-cn" and d["text"] == "英国作家" for d in descriptions)
    assert any(d["lang"] == "zh-tw" and d["text"] == "英國作家" for d in descriptions)
    assert not any(d["lang"] == "de" for d in descriptions)
    assert not any(d["lang"] == "fr" for d in descriptions)


@patch("catalog.sites.wikidata.SITE_PREFERRED_LANGUAGES", ["en", "zh"])
def test_preferred_languages_expansion():
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
        assert content.lookup_ids["imdb"] == "tt0133093"
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
        assert "Q9545711" in content.metadata["director"]  # Lana Wachowski ID
        assert "Q9544977" in content.metadata["director"]  # Lilly Wachowski ID
        assert "Q471839" in content.metadata["genre"]  # Science fiction film
        assert "Q188473" in content.metadata["genre"]  # Action film
        assert "Q1860" in content.metadata["language"]  # English
        assert content.lookup_ids["imdb"] == "tt0133093"

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
        assert "Q1172164" in content.metadata["developer"]  # CD Projekt Red
        assert "Q1172164" in content.metadata["publisher"]
        assert "Q5014725" in content.metadata["platform"]  # Windows
        assert "Q13361286" in content.metadata["platform"]  # PlayStation 4
        assert "Q1422746" in content.metadata["genre"]  # Action RPG
        assert content.metadata["official_site"] == "https://www.cyberpunk.net"
        assert content.lookup_ids["steam"] == "1091500"

    @use_local_response
    def test_scrape_performance(self):
        site = WikiData(url="https://www.wikidata.org/wiki/Q41567")
        content = site.scrape()
        assert content.metadata["title"] == "Hamlet"
        assert content.metadata["preferred_model"] == "Performance"
        assert "Q692" in content.metadata["playwright"]  # Shakespeare
        assert "Q1860" in content.metadata["language"]  # English
        assert "Q80930" in content.metadata["genre"]  # Tragedy
        assert content.metadata["opening_date"] == "1602-00-00"
