"""
Wikidata API integration

Uses the Wikidata REST API: https://www.wikidata.org/wiki/Wikidata:REST_API
"""

import json

import httpx
from django.conf import settings
from loguru import logger

from catalog.book.models import *
from catalog.common import *
from catalog.movie.models import Movie
from catalog.tv.models import TVEpisode, TVSeason, TVShow
from common.models.lang import SITE_PREFERRED_LANGUAGES


# Wikidata Entity IDs for classification
class WikidataTypes:
    # Instance of (P31) values
    HUMAN = "Q5"  # Person
    FILM = "Q11424"  # Film/Movie
    LITERARY_WORK = "Q7725634"  # Literary work (Book)
    NOVEL = "Q8261"  # Novel (specific type of book)
    TV_SERIES = "Q5398426"  # Television series
    TV_SEASON = "Q3464665"  # Television season
    TV_EPISODE = "Q21191270"  # Television episode
    MEDIA_FRANCHISE = "Q134556"  # Media franchise/series


def _get_preferred_languages():
    """Get preferred languages, with special handling for Chinese variants"""
    preferred = []
    for lang in SITE_PREFERRED_LANGUAGES:
        if lang == "zh":
            # Add all Chinese variants
            preferred.extend(
                [
                    "zh",
                    # "zh-cn",
                    # "zh-tw",
                    # "zh-hk",
                    "zh-hans",
                    "zh-hant",
                    # "zh-sg",
                    # "zh-mo",
                ]
            )
        else:
            preferred.append(lang)
    return preferred


WIKIDATA_PREFERRED_LANGS = _get_preferred_languages()


@SiteManager.register
class WikiData(AbstractSite):
    """
    Wikidata site integration using the REST API

    Handles entity retrieval and metadata extraction from Wikidata
    """

    SITE_NAME = SiteName.WikiData
    ID_TYPE = IdType.WikiData
    WIKI_PROPERTY_ID = (
        None  # Wikidata itself doesn't have a property ID in its own system
    )
    DEFAULT_MODEL = None  # Will be determined based on entity type
    URL_PATTERNS = [
        r"^\w+://www\.wikidata\.org/wiki/(Q\d+)",  # Entity URLs like Q12345
        r"^\w+://www\.wikidata\.org/entity/(Q\d+)",  # Entity URLs in alternate format
    ]

    @classmethod
    def id_to_url(cls, id_value):
        """Convert a Wikidata ID to URL"""
        return f"https://www.wikidata.org/wiki/{id_value}"

    def _get_api_client(self):
        """Get API client for Wikidata REST API"""
        headers = {
            "User-Agent": f"NeoDB/WikiData Integration ({settings.SITE_DOMAIN})",
            "Accept": "application/json",
        }

        return httpx.Client(headers=headers)

    def _fetch_entity(self):
        """Fetch entity data from Wikidata REST API"""
        if not self.id_value or not self.id_value.startswith("Q"):
            logger.error(f"Invalid Wikidata ID: {self.id_value}")
            return None

        entity_id = self.id_value
        # Updated to v1 of the API
        api_url = f"https://www.wikidata.org/w/rest.php/wikibase/v1/entities/items/{entity_id}"

        try:
            client = self._get_api_client()
            response = client.get(api_url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"HTTP error fetching Wikidata entity {entity_id}: {e}")
            return None
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON response for Wikidata entity {entity_id}")
            return None

    def _extract_labels(self, entity_data):
        """Extract labels only in preferred languages"""
        labels = {}

        if not entity_data or "labels" not in entity_data:
            return labels

        # Only extract labels in preferred languages
        for lang in WIKIDATA_PREFERRED_LANGS:
            if lang in entity_data["labels"]:
                label_data = entity_data["labels"][lang]
                # Handle both v0 and v1 API formats
                if isinstance(label_data, dict) and "value" in label_data:
                    # v0 API format: {"en": {"value": "Douglas Adams", "language": "en"}}
                    labels[lang] = label_data["value"]
                else:
                    # v1 API format: {"en": "Douglas Adams"}
                    labels[lang] = label_data

        return labels

    def _extract_descriptions(self, entity_data):
        """Extract descriptions only in preferred languages"""
        descriptions = []

        if not entity_data or "descriptions" not in entity_data:
            return descriptions

        # Extract descriptions only for preferred languages
        for lang in WIKIDATA_PREFERRED_LANGS:
            if lang in entity_data["descriptions"]:
                desc_data = entity_data["descriptions"][lang]
                # Handle both v0 and v1 API formats
                if isinstance(desc_data, dict) and "value" in desc_data:
                    # v0 API format: {"en": {"value": "English writer", "language": "en"}}
                    text = desc_data["value"]
                else:
                    # v1 API format: {"en": "English writer"}
                    text = desc_data

                descriptions.append({"lang": lang, "text": text})

        return descriptions

    def _extract_property_value(self, entity_data, property_id):
        """Extract a property value from entity data"""
        if not entity_data:
            return None

        # v1 API uses "statements" instead of "claims"
        claims_key = "statements" if "statements" in entity_data else "claims"

        if claims_key not in entity_data or property_id not in entity_data[claims_key]:
            return None

        claims = entity_data[claims_key][property_id]
        if not claims:
            return None

        # Just get the first value for now - could be expanded for multiple values
        claim = claims[0]

        # v1 API has a different structure
        if "value" in claim:
            return claim["value"]

        # v0 API structure
        if "mainsnak" not in claim or "datavalue" not in claim["mainsnak"]:
            return None

        return claim["mainsnak"]["datavalue"].get("value")

    def _determine_entity_type(self, entity_data):
        """Determine the type of entity and appropriate model based on 'instance of' properties"""
        # Extract 'instance of' (P31) values
        instance_of_values = []

        # Get the appropriate key based on API version
        claims_key = "statements" if "statements" in entity_data else "claims"
        p31_key = "P31"

        # Check if P31 (instance of) exists
        if claims_key in entity_data and p31_key in entity_data[claims_key]:
            claims = entity_data[claims_key][p31_key]
            print(claims)
            # Extract all instance of values
            for claim in claims:
                # Handle different API formats (v0 vs v1)
                if "value" in claim:
                    # v1 API format
                    if isinstance(claim["value"], dict):
                        if "id" in claim["value"]:
                            instance_of_values.append(claim["value"]["id"])
                        elif "content" in claim["value"]:
                            instance_of_values.append(claim["value"]["content"])
                elif "mainsnak" in claim and "datavalue" in claim["mainsnak"]:
                    # v0 API format
                    datavalue = claim["mainsnak"]["datavalue"]
                    if isinstance(
                        datavalue.get("value"), dict
                    ) and "id" in datavalue.get("value", {}):
                        instance_of_values.append(datavalue["value"]["id"])

        # Determine model based on instance of values
        if WikidataTypes.FILM in instance_of_values:
            return Movie
        elif WikidataTypes.TV_SERIES in instance_of_values:
            return TVShow
        elif WikidataTypes.TV_SEASON in instance_of_values:
            return TVSeason
        elif WikidataTypes.TV_EPISODE in instance_of_values:
            return TVEpisode
        elif (
            WikidataTypes.LITERARY_WORK in instance_of_values
            or WikidataTypes.NOVEL in instance_of_values
            or WikidataTypes.MEDIA_FRANCHISE in instance_of_values
        ):
            # Media franchises are typically treated as Works (books) in our system
            return Work
        elif WikidataTypes.HUMAN in instance_of_values:
            # Human entities are not supported in our system yet
            raise ParseError(
                self,
                f"Entity {self.id_value} is a person (Q5). Person entities are not supported.",
            )

        # If no matching model is found and entity has no instance of values
        if not instance_of_values:
            raise ParseError(
                self, f"Entity {self.id_value} has no 'instance of' (P31) properties"
            )

        # If has instance of values but none match our supported types
        logger.warning(
            f"Unable to determine entity type for {self.id_value}. Instance values: {instance_of_values}"
        )
        raise ParseError(
            self,
            f"Entity {self.id_value} has unsupported type(s): {', '.join(instance_of_values)}",
        )

    def _extract_cover_image(self, entity_data):
        """Extract cover image URL from P18 (image) property"""
        if not entity_data:
            return None

        # P18 is the Wikidata property for images
        image_value = self._extract_property_value(entity_data, "P18")
        if not image_value:
            return None

        # Extract the filename - handle different API versions
        if isinstance(image_value, dict):
            # v0 API format may have nested structure
            filename = image_value.get("text", None) or image_value.get("content", None)
        else:
            # v1 API might return the filename directly as string
            filename = image_value

        if not filename:
            return None

        # For Commons images, we need to construct the URL from the filename
        # Format: https://commons.wikimedia.org/wiki/Special:FilePath/{filename}?width=1000
        # This special URL will redirect to the actual image with the specified width
        return (
            f"https://commons.wikimedia.org/wiki/Special:FilePath/{filename}?width=1000"
        )

    def scrape(self) -> ResourceContent:
        """Scrape data from Wikidata API"""
        entity_data = self._fetch_entity()
        if not entity_data:
            logger.error(f"Failed to fetch data for Wikidata entity {self.id_value}")
            return ResourceContent()

        # Extract labels (titles)
        labels = self._extract_labels(entity_data)
        title = next(iter(labels.values())) if labels else self.id_value

        # Extract descriptions
        descriptions = self._extract_descriptions(entity_data)

        # Extract cover image URL
        cover_image_url = self._extract_cover_image(entity_data)

        # Create resource content
        data = ResourceContent()

        # Set basic metadata
        data.metadata = {
            "title": title,
            "localized_title": [
                {"lang": lang, "text": text} for lang, text in labels.items()
            ],
            "localized_description": descriptions,
            "wikidata_entity_type": entity_data.get("type", "item"),
            "external_url": self.url,
        }

        # Add cover image URL if available
        if cover_image_url:
            data.metadata["cover_image_url"] = cover_image_url

        # Set lookup IDs
        data.lookup_ids = {
            "wikidata": self.id_value,
        }

        # Determine entity type for model
        model = self._determine_entity_type(entity_data)
        self.DEFAULT_MODEL = model

        # Add preferred_model to metadata
        if model:
            data.metadata["preferred_model"] = model.__name__

        return data
