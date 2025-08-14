"""
Tests for the WikiData site implementation
"""

from unittest.mock import MagicMock, patch

from catalog.models import IdType
from catalog.sites.wikidata import WikiData, WikidataTypes


class TestWikiData:
    def test_url_parsing(self):
        """Test URL parsing for Wikidata entities"""
        # Test movie URL
        movie_url = "https://www.wikidata.org/wiki/Q83495"  # The Matrix
        site = WikiData(url=movie_url)

        assert site.url == movie_url
        assert site.ID_TYPE == IdType.WikiData
        assert site.id_value == "Q83495"

        # Test alternate URL format
        alt_url = "https://www.wikidata.org/entity/Q83495"
        site2 = WikiData(url=alt_url)
        assert site2.id_value == "Q83495"

    def test_scrape_movie(self, monkeypatch):
        """Test scraping a movie entity from Wikidata"""
        # Patch the WIKIDATA_PREFERRED_LANGS
        monkeypatch.setattr(
            "catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS", ["en", "zh"]
        )

        # Mock API response for The Matrix
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "Q83495",
            "type": "item",
            "labels": {
                "en": {"value": "The Matrix", "language": "en"},
                "zh": {"value": "黑客帝国", "language": "zh"},
            },
            "descriptions": {
                "en": {"value": "1999 science fiction action film", "language": "en"}
            },
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
                ],
                "P18": [
                    {
                        "mainsnak": {
                            "snaktype": "value",
                            "property": "P18",
                            "datatype": "commonsMedia",
                            "datavalue": {
                                "value": "The Matrix Poster.jpg",
                                "type": "string",
                            },
                        }
                    }
                ],
            },
        }

        # Setup mock client with context manager support
        with patch("catalog.sites.wikidata.httpx.Client") as mock_httpx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_response
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=None)
            mock_httpx.return_value = mock_client

            # Create site and scrape
            site = WikiData(url="https://www.wikidata.org/wiki/Q83495")
            content = site.scrape()

            # Verify basic metadata
            assert content.metadata["title"] == "The Matrix"
            assert content.lookup_ids["wikidata"] == "Q83495"

            # Verify localized titles are extracted correctly
            localized_titles = content.metadata["localized_title"]
            assert any(
                t["lang"] == "en" and t["text"] == "The Matrix"
                for t in localized_titles
            )
            assert any(
                t["lang"] == "zh" and t["text"] == "黑客帝国" for t in localized_titles
            )

            # Verify descriptions are extracted correctly
            descriptions = content.metadata["localized_description"]
            assert any(
                d["lang"] == "en" and d["text"] == "1999 science fiction action film"
                for d in descriptions
            )

            # Verify movie entity type is detected correctly
            assert content.metadata["preferred_model"] == "Movie"

            # Verify cover image extraction
            assert "cover_image_url" in content.metadata
            assert (
                content.metadata["cover_image_url"]
                == "https://commons.wikimedia.org/wiki/Special:FilePath/The%20Matrix%20Poster.jpg?width=1000"
            )

            # Verify API client creation
            mock_httpx.assert_called_once()
            mock_client.get.assert_called_once()

    def test_v1_api_format(self, monkeypatch):
        """Test scraping with the v1 API format"""
        # Patch the WIKIDATA_PREFERRED_LANGS
        monkeypatch.setattr(
            "catalog.sites.wikidata.WIKIDATA_PREFERRED_LANGS", ["en", "zh"]
        )

        # Mock API response in v1 format
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "Q83495",
            "type": "item",
            "labels": {
                "en": "The Matrix",
                "zh": "黑客帝国",
            },
            "descriptions": {"en": "1999 science fiction action film"},
            "statements": {
                "P31": [
                    {
                        "value": {"id": WikidataTypes.FILM},
                    }
                ],
                "P18": [{"value": "The Matrix Poster.jpg"}],
            },
        }

        # Setup mock client with context manager support
        with patch("catalog.sites.wikidata.httpx.Client") as mock_httpx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_response
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=None)
            mock_httpx.return_value = mock_client

            # Create site and scrape
            site = WikiData(url="https://www.wikidata.org/wiki/Q83495")
            content = site.scrape()

            # Verify v1 API format is handled correctly
            assert content.metadata["title"] == "The Matrix"
            assert content.metadata["preferred_model"] == "Movie"
            assert (
                content.metadata["cover_image_url"]
                == "https://commons.wikimedia.org/wiki/Special:FilePath/The%20Matrix%20Poster.jpg?width=1000"
            )
