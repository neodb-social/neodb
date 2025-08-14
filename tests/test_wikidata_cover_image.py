"""
Tests for WikiData cover image extraction functionality
"""

from unittest.mock import MagicMock, patch

from catalog.sites.wikidata import WikiData


def test_extract_cover_image_v0_api():
    """Test cover image extraction with v0 API format"""
    # Mock entity data with P18 image in v0 API format
    entity_data = {
        "id": "Q42",
        "claims": {
            "P18": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P18",
                        "datatype": "commonsMedia",
                        "datavalue": {
                            "value": "Douglas_adams_portrait_cropped.jpg",
                            "type": "string",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q42")
    image_url = wiki_site._extract_cover_image(entity_data)

    assert (
        image_url
        == "https://commons.wikimedia.org/wiki/Special:FilePath/Douglas_adams_portrait_cropped.jpg?width=1000"
    )


def test_extract_cover_image_v1_api():
    """Test cover image extraction with v1 API format"""
    # Mock entity data with P18 image in v1 API format
    entity_data = {
        "id": "Q42",
        "statements": {"P18": [{"value": "Douglas_adams_portrait_cropped.jpg"}]},
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q42")
    image_url = wiki_site._extract_cover_image(entity_data)

    assert (
        image_url
        == "https://commons.wikimedia.org/wiki/Special:FilePath/Douglas_adams_portrait_cropped.jpg?width=1000"
    )


def test_extract_cover_image_v1_api_complex():
    """Test cover image extraction with v1 API complex format"""
    # Mock entity data with P18 image in v1 API format with nested structure
    entity_data = {
        "id": "Q42",
        "statements": {
            "P18": [{"value": {"content": "Douglas_adams_portrait_cropped.jpg"}}]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q42")
    image_url = wiki_site._extract_cover_image(entity_data)

    assert (
        image_url
        == "https://commons.wikimedia.org/wiki/Special:FilePath/Douglas_adams_portrait_cropped.jpg?width=1000"
    )


def test_extract_cover_image_no_p18():
    """Test cover image extraction when P18 is not present"""
    # Mock entity data without P18 property
    entity_data = {
        "id": "Q42",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datavalue": {
                            "value": {"id": "Q5", "type": "wikibase-entityid"},
                            "type": "wikibase-entityid",
                        },
                    }
                }
            ]
        },
    }

    wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q42")
    image_url = wiki_site._extract_cover_image(entity_data)

    assert image_url is None


def test_cover_image_in_metadata():
    """Test that cover image URL is added to metadata when available"""
    # Mock API entity response with image
    mock_entity_response = MagicMock()
    mock_entity_response.status_code = 200
    mock_entity_response.json.return_value = {
        "id": "Q42",
        "type": "item",
        "claims": {
            "P31": [
                {
                    "mainsnak": {
                        "snaktype": "value",
                        "property": "P31",
                        "datavalue": {
                            "value": {"id": "Q5", "type": "wikibase-entityid"},
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
                            "value": "Douglas_adams_portrait_cropped.jpg",
                            "type": "string",
                        },
                    }
                }
            ],
        },
        "labels": {"en": {"value": "Douglas Adams", "language": "en"}},
    }

    with (
        patch("catalog.sites.wikidata.httpx.Client") as mock_client,
        patch(
            "catalog.sites.wikidata.WikiData._determine_entity_type"
        ) as mock_determine_type,
    ):
        # Mock client and type determination to avoid errors
        mock_client_instance = MagicMock()
        mock_client_instance.get.return_value = mock_entity_response
        mock_client.return_value = mock_client_instance
        mock_determine_type.return_value = None

        # Create WikiData site and scrape
        wiki_site = WikiData(url="https://www.wikidata.org/wiki/Q42")
        content = wiki_site.scrape()

        # Verify cover_image_url is set correctly in metadata
        assert "cover_image_url" in content.metadata
        assert (
            content.metadata["cover_image_url"]
            == "https://commons.wikimedia.org/wiki/Special:FilePath/Douglas_adams_portrait_cropped.jpg?width=1000"
        )
