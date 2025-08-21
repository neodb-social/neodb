"""
Wikidata API integration

Uses the Wikidata REST API: https://www.wikidata.org/wiki/Wikidata:REST_API
"""

from urllib.parse import quote

import httpx
from loguru import logger

from catalog.common import (
    AbstractSite,
    IdType,
    ParseError,
    ResourceContent,
    SiteManager,
    SiteName,
)
from catalog.common.downloaders import BasicDownloader
from catalog.models import (
    Game,
    Movie,
    Performance,
    Podcast,
    PodcastEpisode,
    TVEpisode,
    TVSeason,
    TVShow,
    Work,
)
from common.models.lang import SITE_PREFERRED_LANGUAGES


# Wikidata Entity IDs for classification
class WikidataTypes:
    # Instance of (P31) values
    HUMAN = "Q5"  # Person
    FILM = "Q11424"  # Film/Movie
    ANIME = "Q1107"  # too general, not mapping
    LITERARY_WORK = "Q7725634"  # Literary work (Book)
    NOVEL = "Q8261"  # Novel (specific type of book)
    TV_SERIES = "Q5398426"  # Television series
    TV_SEASON = "Q3464665"  # Television season
    TV_EPISODE = "Q21191270"  # Television episode
    TV_SPECIAL = "Q1261214"  # Television special
    TV_PROGRAM = "Q15416"  # Television program
    TV_MINISERIES = "Q1259759"  # Miniseries/Limited series
    TV_FILM = "Q506240"  # Television film/TV movie
    MEDIA_FRANCHISE = "Q134556"  # Media franchise/series
    GAME = "Q11410"
    VIDEO_GAME = "Q7889"  # Video game
    VIDEO_GAME_MOD = "Q865493"
    VIDEO_GAME_EXPANSION = "Q209163"
    VIDEO_GAME_EXPANSION2 = "Q107466928"
    VIDEO_GAME_DLC = "Q1066707"
    BOARD_GAME = "Q131436"
    TABLETOP_GAME = "Q3244175"
    PODCAST_SHOW = "Q24634210"  # Podcast show/series
    PODCAST_EPISODE = "Q61855877"  # Podcast episode
    DRAMATIC_WORKS = "Q116476516"  # Dramatic work
    PLAY = "Q25379"  # Theatrical play
    MUSICAL = "Q2743"  # Musical
    OPERA = "Q1344"  # Opera
    PERFORMING_ARTS_PRODUCTION = "Q43099500"  # Performing arts production
    # Anime and manga types
    ANIMATED_FILM = "Q202866"  # Animated film
    ANIME_FILM = "Q20650540"  # Anime film
    ANIME_TV_SERIES = "Q63952888"  # Anime television series
    ANIME_TV_PROGRAM = "Q11086742"  # Anime television program
    ANIMATED_SERIES = "Q581714"  # Animated series
    ANIMATED_TV_SERIES = "Q117467246"  # Animated television series
    OVA = "Q220898"  # Original Video Animation
    OVA_SERIES = "Q113687694"  # Original Video Animation Series
    ONA_SERIES = "Q113671041"  # Original Net Animation series
    SILENT_FILM = "Q226730"  # Silent film
    SHORT_FILM = "Q24862"  # Short film
    FILM_PROJECT = "Q18011172"  # Film project (unpublished or unfinished film)
    MANGA_SERIES = "Q21198342"  # Manga series


# Wikidata Properties for metadata extraction
class WikidataProperties:
    # Core properties
    P18 = "P18"  # image
    P31 = "P31"  # instance of
    P154 = "P154"  # logo image
    P279 = "P279"  # subclass of
    P2716 = "P2716"  # collage image
    P3383 = "P3383"  # film poster

    # Common metadata
    P50 = "P50"  # author
    P57 = "P57"  # director
    P86 = "P86"  # composer
    P136 = "P136"  # genre
    P144 = "P144"  # based on
    P161 = "P161"  # cast member
    P170 = "P170"  # creator
    P175 = "P175"  # performer
    P178 = "P178"  # developer
    P179 = "P179"  # part of the series
    P272 = "P272"  # production company
    P275 = "P275"  # copyright license
    P276 = "P276"  # location
    P287 = "P287"  # designed by
    P291 = "P291"  # place of publication
    P364 = "P364"  # original language
    P371 = "P371"  # presenter
    P400 = "P400"  # platform
    P404 = "P404"  # game mode
    P407 = "P407"  # language of work
    P408 = "P408"  # software engine
    P433 = "P433"  # issue/episode number
    P437 = "P437"  # distribution format
    P449 = "P449"  # original broadcaster
    P453 = "P453"  # guest
    P495 = "P495"  # country of origin
    P571 = "P571"  # inception
    P577 = "P577"  # publication date
    P580 = "P580"  # start time
    P582 = "P582"  # end time
    P674 = "P674"  # characters
    P710 = "P710"  # participant
    P750 = "P750"  # distributed by
    P856 = "P856"  # official website
    P921 = "P921"  # main subject
    P953 = "P953"  # full work available at URL
    P1113 = "P1113"  # number of episodes
    P1476 = "P1476"  # title
    P1809 = "P1809"  # choreographer
    P2047 = "P2047"  # duration
    P2408 = "P2408"  # set in period
    P2437 = "P2437"  # season
    P2438 = "P2438"  # narrator
    P2515 = "P2515"  # set designer
    P2860 = "P2860"  # cites work
    P3300 = "P3300"  # musical conductor
    P5028 = "P5028"  # sound designer
    P5029 = "P5029"  # costume designer
    P5030 = "P5030"  # lighting designer

    # External identifiers
    P123 = "P123"  # publisher
    P212 = "P212"  # ISBN-13
    P345 = "P345"  # IMDb ID
    P436 = "P436"  # MusicBrainz release group ID
    P675 = "P675"  # Google Books ID
    P957 = "P957"  # ISBN-10
    P1712 = "P1712"  # Metacritic ID
    P1733 = "P1733"  # Steam application ID
    P1954 = "P1954"  # Discogs master ID
    P2002 = "P2002"  # Twitter username
    P2003 = "P2003"  # Instagram username
    P2013 = "P2013"  # Facebook ID
    P2206 = "P2206"  # Discogs release ID
    P2339 = "P2339"  # BoardGameGeek ID
    P2397 = "P2397"  # YouTube channel ID
    P2969 = "P2969"  # Goodreads edition ID
    P4529 = "P4529"  # Douban film ID
    P4947 = "P4947"  # TMDb movie ID
    P4983 = "P4983"  # TMDb TV series ID
    P5732 = "P5732"  # Bangumi subject ID
    P5794 = "P5794"  # IGDB game ID
    P5831 = "P5831"  # Spotify show ID
    P5842 = "P5842"  # Apple Podcasts podcast ID
    P6442 = "P6442"  # Douban book version/edition ID
    P6443 = "P6443"  # Douban drama ID
    P6444 = "P6444"  # Douban game ID
    P8383 = "P8383"  # Goodreads work ID
    P8419 = "P8419"  # Archive of Our Own tag
    P10319 = "P10319"  # Douban book works ID

    IdTypeMapping = {
        "P345": IdType.IMDB,
        "P4529": IdType.DoubanMovie,
        "P6444": IdType.DoubanGame,
        "P6443": IdType.DoubanDrama,
        "P6442": IdType.DoubanBook,  # Douban book version/edition ID
        "P10319": IdType.DoubanBook_Work,  # Douban book works ID
        "P1733": IdType.Steam,
        "P5794": IdType.IGDB,
        "P2339": IdType.BGG,
        "P5732": IdType.Bangumi,
        "P212": IdType.ISBN,  # ISBN-13
        "P957": IdType.ISBN10,  # ISBN-10
        "P2969": IdType.Goodreads,  # Goodreads edition ID
        "P8383": IdType.Goodreads_Work,  # Goodreads work ID
        "P675": IdType.GoogleBooks,
        "P4947": IdType.TMDB_Movie,  # TMDb movie ID
        "P4983": IdType.TMDB_TV,  # TMDb TV series ID
        "P1954": IdType.Discogs_Master,  # Discogs master ID
        "P2206": IdType.Discogs_Release,  # Discogs release ID
        # "P436": IdType.MusicBrainz,  # MusicBrainz release group ID
        # "P5842": IdType.ApplePodcasts,
        "P5831": IdType.Spotify_Album,
    }


def _get_preferred_languages():
    """Get preferred languages, with special handling for Chinese variants"""
    preferred = []
    for lang in SITE_PREFERRED_LANGUAGES:
        if lang == "zh":
            # Add all Chinese variants
            preferred.extend(
                [
                    "zh",
                    "zh-cn",
                    "zh-tw",
                    "zh-hk",
                    "zh-hans",
                    "zh-hant",
                    "zh-sg",
                    "zh-mo",
                    "zh-my",
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

    # Map of Wikidata entity types to NeoDB models
    TYPE_TO_MODEL_MAP = {
        WikidataTypes.FILM: Movie,
        WikidataTypes.ANIME_FILM: Movie,
        WikidataTypes.ANIMATED_FILM: Movie,
        WikidataTypes.SILENT_FILM: Movie,
        WikidataTypes.TV_FILM: Movie,
        WikidataTypes.OVA: Movie,
        WikidataTypes.SHORT_FILM: Movie,
        WikidataTypes.FILM_PROJECT: Movie,
        WikidataTypes.TV_SPECIAL: Movie,  # Treat special episodes as Movie
        WikidataTypes.TV_SERIES: TVShow,
        WikidataTypes.ANIME_TV_SERIES: TVShow,
        WikidataTypes.ANIME_TV_PROGRAM: TVShow,
        WikidataTypes.TV_PROGRAM: TVShow,
        WikidataTypes.ANIMATED_SERIES: TVShow,
        WikidataTypes.ANIMATED_TV_SERIES: TVShow,
        WikidataTypes.TV_MINISERIES: TVShow,
        WikidataTypes.OVA_SERIES: TVShow,
        WikidataTypes.ONA_SERIES: TVShow,
        WikidataTypes.TV_SEASON: TVSeason,
        WikidataTypes.TV_EPISODE: TVEpisode,
        WikidataTypes.GAME: Game,
        WikidataTypes.VIDEO_GAME: Game,
        WikidataTypes.VIDEO_GAME_MOD: Game,
        WikidataTypes.VIDEO_GAME_EXPANSION: Game,
        WikidataTypes.VIDEO_GAME_EXPANSION2: Game,
        WikidataTypes.VIDEO_GAME_DLC: Game,
        WikidataTypes.BOARD_GAME: Game,
        WikidataTypes.TABLETOP_GAME: Game,
        WikidataTypes.PODCAST_SHOW: Podcast,
        WikidataTypes.PODCAST_EPISODE: PodcastEpisode,
        WikidataTypes.PLAY: Performance,
        WikidataTypes.MUSICAL: Performance,
        WikidataTypes.OPERA: Performance,
        WikidataTypes.PERFORMING_ARTS_PRODUCTION: Performance,
        WikidataTypes.DRAMATIC_WORKS: Performance,
        WikidataTypes.LITERARY_WORK: Work,
        WikidataTypes.NOVEL: Work,
        WikidataTypes.MEDIA_FRANCHISE: Work,
        WikidataTypes.MANGA_SERIES: Work,
    }

    # Types that have priority over all others
    PRIORITY_TYPES = [WikidataTypes.TV_SPECIAL]

    @classmethod
    def id_to_url(cls, id_value):
        """Convert a Wikidata ID to URL"""
        return f"https://www.wikidata.org/wiki/{id_value}"

    def _fetch_entity(self):
        """Fetch entity data from Wikidata REST API"""
        if not self.id_value or not self.id_value.startswith("Q"):
            logger.error(f"Invalid Wikidata ID: {self.id_value}")
            return None
        return self._fetch_entity_by_id(self.id_value)

    def _fetch_entity_by_id(self, entity_id):
        """Fetch entity data from Wikidata REST API for any entity ID"""
        if not entity_id or not entity_id.startswith("Q"):
            logger.error(f"Invalid Wikidata ID: {entity_id}")
            return None

        try:
            # Updated to v1 of the API
            api_url = f"https://www.wikidata.org/w/rest.php/wikibase/v1/entities/items/{entity_id}"
            return BasicDownloader(api_url).download().json()
        except Exception as e:
            logger.error(f"Failed to fetch entity data for {entity_id}: {e}")
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

    def _extract_property_values(self, entity_data, property_id):
        """Extract all property values from entity data (returns list)"""
        if not entity_data:
            return []

        # v1 API uses "statements" instead of "claims"
        claims_key = "statements" if "statements" in entity_data else "claims"

        if claims_key not in entity_data or property_id not in entity_data[claims_key]:
            return []

        claims = entity_data[claims_key][property_id]
        if not claims:
            return []

        values = []
        for claim in claims:
            # v1 API has a different structure
            if "value" in claim:
                values.append(claim["value"])
            # v0 API structure
            elif "mainsnak" in claim and "datavalue" in claim["mainsnak"]:
                value = claim["mainsnak"]["datavalue"].get("value")
                if value:
                    values.append(value)

        return values

    def _extract_string_list(self, entity_data, property_id):
        """Extract a list of strings from property values"""
        values = self._extract_property_values(entity_data, property_id)
        result = []
        for value in values:
            if isinstance(value, str):
                result.append(value)
            elif isinstance(value, dict):
                # Handle entity references
                if "id" in value:
                    # Could resolve entity labels here if needed
                    result.append(value["id"])
                    logger.warning(
                        f"QID not supported {property_id}:{value['id']} for {self.id_value}"
                    )
                elif "text" in value:
                    result.append(value["text"])
                elif "content" in value:
                    result.append(value["content"])
        return result

    def _f_date(self, d: str) -> str:
        return d.replace("-00", "-01")

    def _extract_date(self, entity_data, property_id):
        """Extract a date from property value"""
        value = self._extract_property_value(entity_data, property_id)
        if not value:
            return None
        if "content" in value:
            value = value["content"]
        if isinstance(value, dict):
            # Handle time values
            if "time" in value:
                # Wikidata time format: +YYYY-MM-DDTHH:MM:SSZ
                time_str = value["time"]
                # Extract just the date part
                if time_str.startswith("+"):
                    time_str = time_str[1:]
                if "T" in time_str:
                    return self._f_date(time_str.split("T")[0])
                return self._f_date(time_str)
        elif isinstance(value, str):
            # Already a string date
            return self._f_date(value)

        return None

    def _extract_url(self, entity_data, property_id):
        """Extract a URL from property value"""
        value = self._extract_property_value(entity_data, property_id)
        if not value:
            return None

        if isinstance(value, str):
            return value
        elif isinstance(value, dict):
            # Handle different formats
            if "text" in value:
                return value["text"]
            elif "content" in value:
                return value["content"]

        return None

    def _extract_duration(self, entity_data):
        """Extract duration in seconds from P2047"""
        value = self._extract_property_value(entity_data, WikidataProperties.P2047)
        if not value:
            return None

        if isinstance(value, dict):
            # Wikidata stores duration as quantity
            if "amount" in value:
                # Convert to seconds if needed
                return int(float(value["amount"]))
        elif isinstance(value, (int, float)):
            return int(value)

        return None

    def _extract_entity_types(self, entity_data, property_id):
        """Extract entity types (instance of or subclass of) from a property"""
        type_values = []

        # Get the appropriate key based on API version
        claims_key = "statements" if "statements" in entity_data else "claims"

        # Check if the property exists
        if claims_key in entity_data and property_id in entity_data[claims_key]:
            claims = entity_data[claims_key][property_id]
            # Extract all values
            for claim in claims:
                # Handle different API formats (v0 vs v1)
                if "value" in claim:
                    # v1 API format
                    if isinstance(claim["value"], dict):
                        if "id" in claim["value"]:
                            type_values.append(claim["value"]["id"])
                        elif "content" in claim["value"]:
                            type_values.append(claim["value"]["content"])
                elif "mainsnak" in claim and "datavalue" in claim["mainsnak"]:
                    # v0 API format
                    datavalue = claim["mainsnak"]["datavalue"]
                    if isinstance(
                        datavalue.get("value"), dict
                    ) and "id" in datavalue.get("value", {}):
                        type_values.append(datavalue["value"]["id"])

        return type_values

    def _determine_model_from_entity_types(self, entity_types, entity_id):
        """Map entity types to appropriate model using a mapping dictionary with prioritization

        Special case: TV_SPECIAL takes precedence over other types when an entity has multiple types.
        """
        if not entity_types:
            return None

        # Check for human type (not supported)
        if WikidataTypes.HUMAN in entity_types:
            raise ParseError(
                self,
                f"Entity {entity_id} is a person (Q5). Person entities are not supported.",
            )

        # Check priority types first
        for priority_type in self.PRIORITY_TYPES:
            if (
                priority_type in entity_types
                and priority_type in self.TYPE_TO_MODEL_MAP
            ):
                return self.TYPE_TO_MODEL_MAP[priority_type]

        # Look for any matching type
        for entity_type in entity_types:
            if entity_type in self.TYPE_TO_MODEL_MAP:
                return self.TYPE_TO_MODEL_MAP[entity_type]

        return None

    def _fetch_parent_types(self, entity_data):
        """Fetch the parent types (subclass of) values from entity data"""
        return self._extract_entity_types(entity_data, WikidataProperties.P279)

    def _fetch_parent_types_with_api(self, entity_types, max_depth=1, current_depth=0):
        """Fetch parent types (subclass of) for given entity types using API calls

        This makes API calls to Wikidata for each entity type to find their parent classes.
        Supports recursive lookup up to max_depth levels.

        Args:
            entity_types: List of entity type IDs to look up
            max_depth: Maximum recursion depth for parent lookup
            current_depth: Current recursion depth (internal use)

        Returns:
            List of parent type IDs
        """
        if not entity_types or current_depth >= max_depth:
            return []

        parent_types = []
        # Use a set to avoid duplicate API calls
        processed_types = set()

        for entity_type in entity_types:
            if entity_type in processed_types:
                continue

            processed_types.add(entity_type)

            # Fetch entity data for this type via API
            type_entity_data = self._fetch_entity_by_id(entity_type)
            if not type_entity_data:
                continue

            # Extract subclass of values
            direct_parent_types = self._extract_entity_types(
                type_entity_data, WikidataProperties.P279
            )
            parent_types.extend(direct_parent_types)

            # Recursively fetch parent types of parent types if needed
            if current_depth < max_depth - 1 and direct_parent_types:
                recursive_parent_types = self._fetch_parent_types_with_api(
                    direct_parent_types, max_depth, current_depth + 1
                )
                parent_types.extend(recursive_parent_types)

        # Return unique parent types
        return list(set(parent_types))

    def _determine_entity_type(self, entity_data):
        """Determine the type of entity and appropriate model based on properties

        Uses a multi-level approach to determine the appropriate model:
        1. Direct 'instance of' (P31) values
        2. Direct 'subclass of' (P279) values from the entity
        3. Parent types of instance classes via API lookup (when needed)
        4. Recursive parent lookup up to a configurable depth
        """
        # Extract 'instance of' (P31) values
        instance_of_values = self._extract_entity_types(
            entity_data, WikidataProperties.P31
        )

        if not instance_of_values:
            raise ParseError(
                self, f"Entity {self.id_value} has no 'instance of' (P31) properties"
            )

        # Try to determine model based on instance of values
        model = self._determine_model_from_entity_types(
            instance_of_values, self.id_value
        )
        if model:
            return model

        # If no model found from instance_of, try to look up direct parent classes
        direct_parent_types = self._fetch_parent_types(entity_data)
        if direct_parent_types:
            parent_model = self._determine_model_from_entity_types(
                direct_parent_types, self.id_value
            )
            if parent_model:
                logger.info(
                    f"Determined model {parent_model.__name__} from direct parent type for {self.id_value}"
                )
                return parent_model

        # If still no match, try to fetch parent types of instance classes via API
        # This handles the case where an entity is an instance of a class that is a subclass of a known type
        instance_parent_types = self._fetch_parent_types_with_api(
            instance_of_values, max_depth=2
        )
        if instance_parent_types:
            instance_parent_model = self._determine_model_from_entity_types(
                instance_parent_types, self.id_value
            )
            if instance_parent_model:
                logger.info(
                    f"Determined model {instance_parent_model.__name__} from instance parent type "
                    f"for {self.id_value}"
                )
                return instance_parent_model

        # If we still don't have a match, try recursive parent lookup on direct parent types
        if direct_parent_types:
            recursive_parent_types = self._fetch_parent_types_with_api(
                direct_parent_types, max_depth=3
            )
            if recursive_parent_types:
                recursive_model = self._determine_model_from_entity_types(
                    recursive_parent_types, self.id_value
                )
                if recursive_model:
                    logger.info(
                        f"Determined model {recursive_model.__name__} from recursive parent lookup "
                        f"for {self.id_value}"
                    )
                    return recursive_model

        logger.error(
            f"Entity has unsupported type(s): {', '.join(instance_of_values)}",
            extra={"qid": self.id_value},
        )
        raise ParseError(
            self,
            f"Entity has unsupported type(s): {', '.join(instance_of_values)}",
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
        return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(filename)}?width=1000"

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
        }

        # Add cover image URL if available
        if cover_image_url:
            data.metadata["cover_image_url"] = cover_image_url

        # Set lookup IDs (start with wikidata)
        data.lookup_ids = {}

        # Determine entity type for model
        model = self._determine_entity_type(entity_data)
        self.DEFAULT_MODEL = model

        # Add preferred_model to metadata
        if model:
            data.metadata["preferred_model"] = model.__name__

        # Extract model-specific metadata
        if model == Game:
            self._extract_game_metadata(entity_data, data)
        elif model == Podcast:
            self._extract_podcast_metadata(entity_data, data)
        elif model == PodcastEpisode:
            self._extract_podcast_episode_metadata(entity_data, data)
        elif model == Performance:
            self._extract_performance_metadata(entity_data, data)
        elif model == Movie:
            self._extract_movie_metadata(entity_data, data)
        elif model == TVShow:
            self._extract_tv_show_metadata(entity_data, data)
        elif model == TVSeason:
            self._extract_tv_season_metadata(entity_data, data)
        elif model == TVEpisode:
            self._extract_tv_episode_metadata(entity_data, data)
        elif model == Work:
            self._extract_work_metadata(entity_data, data)

        resources = self._extract_external_ids(entity_data)
        prematched_resources = []
        for res in resources:
            try:
                site_cls = SiteManager.get_site_cls_by_id_type(res["id_type"])
                if (
                    model == site_cls.DEFAULT_MODEL
                    or model in site_cls.MATCHABLE_MODELS
                ):
                    prematched_resources.append(res)
                    data.lookup_ids[res["id_type"]] = res["id_value"]
                else:
                    logger.error(
                        f"IdType {res['id_type']} does not match Model {model}, skipping",
                        extra={
                            "id_type": self.ID_TYPE,
                            "id_value": self.id_value,
                            "prematched": res,
                        },
                    )
            except Exception as e:
                logger.error(
                    f"Error processing {res['id_type']} for {self.id_value}: {e}"
                )
        # data.metadata["prematched_resources"] = prematched_resources
        return data

    def _extract_game_metadata(self, entity_data, data):
        """Extract Game-specific metadata"""
        # Existing model fields
        data.metadata["release_date"] = self._extract_date(
            entity_data, WikidataProperties.P577
        )
        # data.metadata["developer"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P178
        # )
        # data.metadata["publisher"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P123
        # )
        # data.metadata["platform"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P400
        # )
        # data.metadata["genre"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P136
        # )
        # data.metadata["designer"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P287
        # )
        data.metadata["artist"] = []  # No direct Wikidata property for artist
        data.metadata["official_site"] = self._extract_url(
            entity_data, WikidataProperties.P856
        )

        # Additional properties (as comments for future implementation)
        # data.metadata["composer"] = self._extract_string_list(entity_data, WikidataProperties.P86)
        # data.metadata["game_mode"] = self._extract_string_list(entity_data, WikidataProperties.P404)
        # data.metadata["software_engine"] = self._extract_string_list(entity_data, WikidataProperties.P408)
        # data.metadata["distribution_format"] = self._extract_string_list(entity_data, WikidataProperties.P437)
        # data.metadata["distributed_by"] = self._extract_string_list(entity_data, WikidataProperties.P750)
        # data.metadata["influenced_by"] = self._extract_string_list(entity_data, WikidataProperties.P2860)
        # data.metadata["based_on"] = self._extract_string_list(entity_data, WikidataProperties.P144)
        # data.metadata["part_of_series"] = self._extract_property_value(entity_data, WikidataProperties.P179)

    def _extract_podcast_metadata(self, entity_data, data):
        """Extract Podcast-specific metadata"""
        # Existing model fields
        # data.metadata["genre"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P136
        # )
        # data.metadata["host"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P371
        # )
        # data.metadata["language"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P364
        # ) or self._extract_string_list(entity_data, WikidataProperties.P407)
        data.metadata["official_site"] = self._extract_url(
            entity_data, WikidataProperties.P856
        )

        # Additional properties (as comments for future implementation)
        # data.metadata["first_episode_date"] = self._extract_date(entity_data, WikidataProperties.P577)
        # data.metadata["last_episode_date"] = self._extract_date(entity_data, WikidataProperties.P582)
        # data.metadata["creator"] = self._extract_string_list(entity_data, WikidataProperties.P170)
        # data.metadata["episode_count"] = self._extract_property_value(entity_data, WikidataProperties.P1113)
        # data.metadata["original_broadcaster"] = self._extract_string_list(entity_data, WikidataProperties.P449)
        # data.metadata["number_of_seasons"] = self._extract_property_value(entity_data, WikidataProperties.P2437)
        # data.metadata["country_of_origin"] = self._extract_string_list(entity_data, WikidataProperties.P495)
        # data.metadata["main_subject"] = self._extract_string_list(entity_data, WikidataProperties.P921)

        # RSS feed URL
        feed_url = self._extract_url(entity_data, WikidataProperties.P953)
        if feed_url:
            data.lookup_ids["rss"] = feed_url

        # External podcast IDs
        # data.metadata["apple_podcasts_id"] = self._extract_property_value(entity_data, WikidataProperties.P5842)
        # data.metadata["spotify_show_id"] = self._extract_property_value(entity_data, WikidataProperties.P5831)

    def _extract_podcast_episode_metadata(self, entity_data, data):
        """Extract PodcastEpisode-specific metadata"""
        # Existing model fields
        data.metadata["pub_date"] = self._extract_date(
            entity_data, WikidataProperties.P577
        )
        data.metadata["duration"] = self._extract_duration(entity_data)
        data.metadata["guid"] = self._extract_property_value(
            entity_data, WikidataProperties.P433
        )
        data.metadata["media_url"] = self._extract_url(
            entity_data, WikidataProperties.P953
        )
        data.metadata["link"] = self._extract_url(entity_data, WikidataProperties.P856)

        # Additional properties (as comments for future implementation)
        # data.metadata["part_of_series"] = self._extract_property_value(entity_data, WikidataProperties.P179)
        # data.metadata["episode_number"] = self._extract_property_value(entity_data, WikidataProperties.P433)
        # data.metadata["presenter"] = self._extract_string_list(entity_data, WikidataProperties.P371)
        # data.metadata["guest"] = self._extract_string_list(entity_data, WikidataProperties.P453)
        # data.metadata["main_subject"] = self._extract_string_list(entity_data, WikidataProperties.P921)
        # data.metadata["set_in_period"] = self._extract_property_value(entity_data, WikidataProperties.P2408)
        # data.metadata["characters"] = self._extract_string_list(entity_data, WikidataProperties.P674)

    def _extract_performance_metadata(self, entity_data, data):
        """Extract Performance-specific metadata"""
        # Existing model fields
        data.metadata["opening_date"] = self._extract_date(
            entity_data, WikidataProperties.P577
        )
        data.metadata["closing_date"] = self._extract_date(
            entity_data, WikidataProperties.P582
        )
        # data.metadata["language"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P364
        # ) or self._extract_string_list(entity_data, WikidataProperties.P407)
        # data.metadata["genre"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P136
        # )
        # data.metadata["playwright"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P50
        # )
        # data.metadata["composer"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P86
        # )
        # data.metadata["director"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P57
        # )
        # data.metadata["choreographer"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P1809
        # )
        # data.metadata["orig_creator"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P170
        # )
        data.metadata["official_site"] = self._extract_url(
            entity_data, WikidataProperties.P856
        )

        # Cast/Actor (simplified - would need role extraction for full support)
        # cast_members = self._extract_string_list(entity_data, WikidataProperties.P161)
        # data.metadata["actor"] = [{"name": name, "role": None} for name in cast_members]

        # Performer (separate from actors)
        # data.metadata["performer"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P175
        # )

        # Additional properties (as comments for future implementation)
        # data.metadata["location"] = self._extract_string_list(entity_data, WikidataProperties.P276)
        # data.metadata["troupe"] = self._extract_string_list(entity_data, WikidataProperties.P710)
        # data.metadata["country_of_origin"] = self._extract_string_list(entity_data, WikidataProperties.P495)
        # data.metadata["based_on"] = self._extract_string_list(entity_data, WikidataProperties.P144)
        # data.metadata["narrator"] = self._extract_string_list(entity_data, WikidataProperties.P2438)
        # data.metadata["musical_conductor"] = self._extract_string_list(entity_data, WikidataProperties.P3300)
        # data.metadata["lighting_designer"] = self._extract_string_list(entity_data, WikidataProperties.P5030)
        # data.metadata["sound_designer"] = self._extract_string_list(entity_data, WikidataProperties.P5028)
        # data.metadata["costume_designer"] = self._extract_string_list(entity_data, WikidataProperties.P5029)
        # data.metadata["set_designer"] = self._extract_string_list(entity_data, WikidataProperties.P2515)

        # Crew (combine various designers into crew list)
        crew = []
        # Could add lighting, sound, costume, set designers to crew here
        data.metadata["crew"] = crew

    def _extract_movie_metadata(self, entity_data, data):
        """Extract Movie-specific metadata"""
        # Basic movie metadata
        data.metadata["release_date"] = self._extract_date(
            entity_data, WikidataProperties.P577
        )
        # data.metadata["director"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P57
        # )
        # data.metadata["genre"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P136
        # )
        # data.metadata["language"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P364
        # ) or self._extract_string_list(entity_data, WikidataProperties.P407)

        # Additional properties
        # data.metadata["cast"] = self._extract_string_list(entity_data, WikidataProperties.P161)
        # data.metadata["producer"] = self._extract_string_list(entity_data, WikidataProperties.P272)
        # data.metadata["composer"] = self._extract_string_list(entity_data, WikidataProperties.P86)
        # data.metadata["based_on"] = self._extract_string_list(entity_data, WikidataProperties.P144)
        # data.metadata["country_of_origin"] = self._extract_string_list(entity_data, WikidataProperties.P495)

    def _extract_tv_show_metadata(self, entity_data, data):
        """Extract TVShow-specific metadata"""
        data.metadata["first_air_date"] = self._extract_date(
            entity_data, WikidataProperties.P577
        )
        data.metadata["last_air_date"] = self._extract_date(
            entity_data, WikidataProperties.P582
        )
        # data.metadata["genre"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P136
        # )
        # data.metadata["language"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P364
        # ) or self._extract_string_list(entity_data, WikidataProperties.P407)
        data.metadata["number_of_episodes"] = self._extract_property_value(
            entity_data, WikidataProperties.P1113
        )
        data.metadata["number_of_seasons"] = self._extract_property_value(
            entity_data, WikidataProperties.P2437
        )

        # Additional properties
        # data.metadata["creator"] = self._extract_string_list(entity_data, WikidataProperties.P170)
        # data.metadata["cast"] = self._extract_string_list(entity_data, WikidataProperties.P161)
        # data.metadata["original_broadcaster"] = self._extract_string_list(entity_data, WikidataProperties.P449)
        # data.metadata["country_of_origin"] = self._extract_string_list(entity_data, WikidataProperties.P495)

    def _extract_tv_season_metadata(self, entity_data, data):
        """Extract TVSeason-specific metadata"""
        data.metadata["first_air_date"] = self._extract_date(
            entity_data, WikidataProperties.P577
        )
        data.metadata["last_air_date"] = self._extract_date(
            entity_data, WikidataProperties.P582
        )
        data.metadata["number_of_episodes"] = self._extract_property_value(
            entity_data, WikidataProperties.P1113
        )
        # data.metadata["part_of_series"] = self._extract_property_value(
        #     entity_data, WikidataProperties.P179
        # )

        # Additional properties
        # data.metadata["season_number"] = self._extract_property_value(entity_data, WikidataProperties.P2437)

    def _extract_tv_episode_metadata(self, entity_data, data):
        """Extract TVEpisode-specific metadata"""
        data.metadata["air_date"] = self._extract_date(
            entity_data, WikidataProperties.P577
        )
        data.metadata["episode_number"] = self._extract_property_value(
            entity_data, WikidataProperties.P433
        )
        # data.metadata["part_of_series"] = self._extract_property_value(
        #     entity_data, WikidataProperties.P179
        # )
        data.metadata["duration"] = self._extract_duration(entity_data)

        # Additional properties
        # data.metadata["director"] = self._extract_string_list(entity_data, WikidataProperties.P57)
        # data.metadata["cast"] = self._extract_string_list(entity_data, WikidataProperties.P161)

    def _extract_work_metadata(self, entity_data, data):
        """Extract Work (Book/Literary work)-specific metadata"""
        data.metadata["publication_date"] = self._extract_date(
            entity_data, WikidataProperties.P577
        )
        # data.metadata["author"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P50
        # )
        # data.metadata["genre"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P136
        # )
        # data.metadata["language"] = self._extract_string_list(
        #     entity_data, WikidataProperties.P364
        # ) or self._extract_string_list(entity_data, WikidataProperties.P407)

        # Additional properties
        # data.metadata["publisher"] = self._extract_string_list(entity_data, WikidataProperties.P123)
        # data.metadata["country_of_origin"] = self._extract_string_list(entity_data, WikidataProperties.P495)
        # data.metadata["based_on"] = self._extract_string_list(entity_data, WikidataProperties.P144)
        # data.metadata["part_of_series"] = self._extract_property_value(entity_data, WikidataProperties.P179)

    def get_wikipedia_pages(self, entity_data=None):
        """Fetch all Wikipedia pages for this Wikidata entity

        Returns a dictionary of language codes to Wikipedia page URLs.

        Example: {
            "en": "https://en.wikipedia.org/wiki/The_Matrix",
            "zh": "https://zh.wikipedia.org/wiki/黑客帝国",
            ...
        }
        """
        if not entity_data and not self.id_value:
            return {}

        entity_id = self.id_value

        try:
            # Use Wikidata API to get all sitelinks (Wikipedia pages)
            api_url = "https://www.wikidata.org/w/api.php"
            params = {
                "action": "wbgetentities",
                "format": "json",
                "ids": entity_id,
                "props": "sitelinks",
            }

            response = httpx.get(api_url, params=params, timeout=2)
            data = response.json()

            if "entities" not in data or entity_id not in data["entities"]:
                logger.warning(f"No entity data found for {entity_id}")
                return {}

            entity = data["entities"][entity_id]
            if "sitelinks" not in entity:
                logger.warning(f"No sitelinks found for {entity_id}")
                return {}

            # Extract Wikipedia pages
            wiki_pages = {}
            for site_key, site_data in entity["sitelinks"].items():
                # Only include Wikipedia links (skip other projects like Wiktionary)
                if site_key.endswith("wiki") and not site_key.startswith("commons"):
                    lang_code = site_key.replace("wiki", "")
                    title = site_data["title"]
                    url = f"https://{lang_code}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
                    wiki_pages[lang_code] = {"url": url, "title": title}

            return wiki_pages

        except Exception as e:
            logger.error(
                f"Error fetching Wikipedia pages: {e}",
                extra={"QID": entity_id, "exception": e},
            )
            return {}

    def _extract_external_ids(self, entity_data):
        """Extract common external identifiers to lookup_ids"""
        resources = []
        for property_id, id_type in WikidataProperties.IdTypeMapping.items():
            value = self._extract_property_value(entity_data, property_id)
            if value:
                # Handle both v0 and v1 API formats
                if isinstance(value, dict):
                    value = value.get("content") or value.get("text")
                resources.append({"id_type": id_type, "id_value": value})
        return resources

    @classmethod
    def lookup_qid_by_external_id(cls, id_type: IdType, id_value: str) -> str | None:
        """
        Lookup Wikidata QID based on an external identifier.

        Args:
            id_type: The type of identifier (e.g., IdType.Steam, IdType.IMDB, etc.)
            id_value: The value of the identifier

        Returns:
            The Wikidata QID (e.g., "Q12345") if found, None otherwise

        Example:
            qid = WikiData.lookup_qid_by_external_id(IdType.Steam, "730")
            # Returns "Q17279" (Counter-Strike: Global Offensive)
        """
        # Find the Wikidata property ID for this ID type
        property_id = None
        for prop_id, mapped_type in WikidataProperties.IdTypeMapping.items():
            if mapped_type == id_type:
                property_id = prop_id
                break

        if not property_id:
            logger.warning(f"No Wikidata property mapping found for {id_type}")
            return None

        try:
            # Use SPARQL query to find entity with this external ID
            sparql_query = f"""
            SELECT ?item WHERE {{
                ?item wdt:{property_id} "{id_value}".
            }}
            LIMIT 1
            """

            # Query Wikidata SPARQL endpoint
            api_url = "https://query.wikidata.org/sparql"
            params = {"query": sparql_query, "format": "json"}

            # Build URL with parameters
            from urllib.parse import urlencode

            full_url = f"{api_url}?{urlencode(params)}"

            response = BasicDownloader(full_url).download()
            data = response.json()

            # Extract QID from results
            if data.get("results", {}).get("bindings"):
                item_uri = data["results"]["bindings"][0]["item"]["value"]
                # Extract QID from URI (e.g., "http://www.wikidata.org/entity/Q12345" -> "Q12345")
                qid = item_uri.split("/")[-1]
                logger.info(f"Found Wikidata QID {qid} for {id_type}:{id_value}")
                return qid
            else:
                logger.info(f"No Wikidata entity found for {id_type}:{id_value}")
                return None

        except Exception as e:
            logger.error(f"Failed to lookup Wikidata QID for {id_type}:{id_value}: {e}")
            return None
