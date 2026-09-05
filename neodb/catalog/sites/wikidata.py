"""
Wikidata API integration

Uses the Wikidata REST API: https://www.wikidata.org/wiki/Wikidata:REST_API
"""

from urllib.parse import quote, urlencode

from loguru import logger

from catalog.common import (
    AbstractSite,
    IdType,
    ParseError,
    ResourceContent,
    SiteManager,
    SiteName,
)
from catalog.common.downloaders import BasicDownloader, DownloadError
from catalog.models import (
    Album,
    Edition,
    Game,
    Item,
    Movie,
    People,
    Performance,
    Podcast,
    PodcastEpisode,
    TVEpisode,
    TVSeason,
    TVShow,
    Work,
)
from catalog.sites.openlibrary import OpenLibrary
from common.models.lang import SITE_PREFERRED_LANGUAGES


# Wikidata Entity IDs for classification
class WikidataTypes:
    # Instance of (P31) values
    HUMAN = "Q5"  # Person
    FILM = "Q11424"  # Film/Movie
    ANIME = "Q1107"  # too general, not mapping
    CREATIVE_WORK = "Q17537576"
    LITERARY_WORK = "Q7725634"  # Literary work (Book)
    BOOK = "Q571"
    NOVEL = "Q8261"  # Novel (specific type of book)
    WRITTEN_WORK = "Q47461344"
    EDITION = "Q3331189"  # version, edition or translation
    TV_SERIES = "Q5398426"  # Television series
    TV_SEASON = "Q3464665"  # Television season
    TV_EPISODE = "Q21191270"  # Television episode
    TV_SPECIAL = "Q1261214"  # Television special
    TV_PROGRAM = "Q15416"  # Television program
    TV_MINISERIES = "Q1259759"  # Miniseries/Limited series
    TV_FILM = "Q506240"  # Television film/TV movie
    MUSIC_ALBUM = "Q482994"  # album
    MUSIC_SINGLE = "Q134556"  # single
    MUSIC_EP = "Q169930"  # extended play
    VIDEO_ALBUM = "Q10590726"  # video album
    MUSIC_RELEASE_GROUP = "Q108346082"  # release group
    MUSICAL_RELEASE = "Q2031291"  # musical release
    MEDIA_FRANCHISE = "Q196600"  # Media franchise/series
    GAME = "Q11410"
    VIDEO_GAME = "Q7889"  # Video game
    VIDEO_GAME_MOD = "Q865493"
    VIDEO_GAME_EXPANSION = "Q209163"
    VIDEO_GAME_EXPANSION2 = "Q107466928"
    VIDEO_GAME_DLC = "Q1066707"
    BOARD_GAME = "Q131436"
    TABLETOP_GAME = "Q3244175"
    GAME_EMULATOR = "Q1196126"
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
    # Organization types
    BUSINESS_ENTERPRISE = "Q4830453"  # Business enterprise
    PUBLISHER = "Q2085381"  # Publisher
    RECORD_LABEL = "Q18127"  # Record label
    FILM_PRODUCTION_COMPANY = "Q1762059"  # Film production company
    VIDEO_GAME_DEVELOPER = "Q210167"  # Video game developer
    VIDEO_GAME_PUBLISHER = "Q1137109"  # Video game publisher
    ANIMATION_STUDIO = "Q1107679"  # animation studio
    FILM_STUDIO = "Q375336"  # film studio
    THEATER_COMPANY = "Q742421"  # theatre company


# Wikidata Properties NeoDB reads; IdTypeMapping keys stay raw strings
class WikidataProperties:
    IMAGE = "P18"
    INSTANCE_OF = "P31"
    SUBCLASS_OF = "P279"
    ISSUE_NUMBER = "P433"  # issue or episode number
    DATE_OF_BIRTH = "P569"
    DATE_OF_DEATH = "P570"
    PUBLICATION_DATE = "P577"
    END_TIME = "P582"
    OFFICIAL_WEBSITE = "P856"
    WORK_AVAILABLE_AT_URL = "P953"
    NUMBER_OF_EPISODES = "P1113"
    DURATION = "P2047"
    NUMBER_OF_SEASONS = "P2437"

    IdTypeMapping = {
        "P345": IdType.IMDB,
        "P4529": IdType.DoubanMovie,
        "P6444": IdType.DoubanGame,
        "P6443": IdType.DoubanDrama,
        "P6442": IdType.DoubanBook,  # Douban book version/edition ID
        "P10319": IdType.DoubanBook_Work,  # Douban book works ID
        "P1733": IdType.Steam,
        "P5794": IdType.IGDB,
        "P11688": IdType.MobyGames,
        "P2339": IdType.BGG,
        "P5732": IdType.Bangumi,
        "P212": IdType.ISBN,  # ISBN-13
        "P957": IdType.ISBN10,  # ISBN-10
        "P2969": IdType.Goodreads,  # Goodreads edition ID
        "P8383": IdType.Goodreads_Work,  # Goodreads work ID
        "P675": IdType.GoogleBooks,
        "P648": IdType.OpenLibrary,  # Open Library, might be Edition/Work/Person, handled below
        "P4947": IdType.TMDB_Movie,  # TMDb movie ID
        "P4983": IdType.TMDB_TV,  # TMDb TV series ID
        "P1954": IdType.Discogs_Master,  # Discogs master ID
        "P2206": IdType.Discogs_Release,  # Discogs release ID
        "P436": IdType.MusicBrainz_ReleaseGroup,  # MusicBrainz release group ID
        "P5813": IdType.MusicBrainz_Release,  # MusicBrainz release ID
        # "P5842": IdType.ApplePodcasts,
        "P2205": IdType.Spotify_Album,
        "P4300": IdType.YouTubeMusic,  # YouTube playlist ID (YouTube Music album)
        "P8729": IdType.AniList_Anime,
        "P8731": IdType.AniList_Manga,
        "P4086": IdType.MAL_Anime,
        "P4087": IdType.MAL_Manga,
        "P11149": IdType.MangaUpdates,
        # Person-specific
        "P4985": IdType.TMDB_Person,
        "P2963": IdType.Goodreads_Author,
        "P1902": IdType.Spotify_Artist,
        "P9650": IdType.IGDB_Company,
        "P12836": IdType.DoubanPersonage,
        "P434": IdType.MusicBrainz_Artist,
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

# 'subclass of' values per class QID; the same classes recur across imports
_PARENT_TYPE_CACHE: dict[str, list[str]] = {}


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
    MATCHABLE_MODELS = [
        Movie,
        TVShow,
        TVSeason,
        TVEpisode,
        Game,
        Podcast,
        PodcastEpisode,
        Performance,
        Work,
        Album,
        Edition,
        People,
    ]

    # Map of Wikidata entity types to NeoDB models
    TYPE_TO_MODEL_MAP = {
        WikidataTypes.HUMAN: People,
        WikidataTypes.BUSINESS_ENTERPRISE: People,
        WikidataTypes.PUBLISHER: People,
        WikidataTypes.RECORD_LABEL: People,
        WikidataTypes.FILM_PRODUCTION_COMPANY: People,
        WikidataTypes.VIDEO_GAME_DEVELOPER: People,
        WikidataTypes.VIDEO_GAME_PUBLISHER: People,
        WikidataTypes.ANIMATION_STUDIO: People,
        WikidataTypes.FILM_STUDIO: People,
        WikidataTypes.THEATER_COMPANY: People,
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
        WikidataTypes.GAME_EMULATOR: Game,
        WikidataTypes.MUSIC_ALBUM: Album,
        WikidataTypes.MUSIC_SINGLE: Album,
        WikidataTypes.MUSIC_EP: Album,
        WikidataTypes.VIDEO_ALBUM: Album,
        WikidataTypes.MUSIC_RELEASE_GROUP: Album,
        WikidataTypes.MUSICAL_RELEASE: Album,
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
        WikidataTypes.BOOK: Work,
        WikidataTypes.CREATIVE_WORK: Work,
        WikidataTypes.WRITTEN_WORK: Work,
        WikidataTypes.EDITION: Edition,
    }

    # Types that have priority over all others
    PRIORITY_TYPES = [WikidataTypes.TV_SPECIAL]

    # Classes too generic to identify a category on their own. They still count
    # as a direct 'instance of' value, but are ignored while walking up the
    # subclass graph: they sit above nearly every creative type, so matching
    # them there turns any unmapped work into a book.
    AMBIGUOUS_ANCESTOR_TYPES = frozenset({WikidataTypes.CREATIVE_WORK})

    # Levels of 'subclass of' to walk when no direct type matches
    MAX_DEPTH = 3

    @classmethod
    def id_to_url(cls, id_value):
        """Convert a Wikidata ID to URL"""
        return f"https://www.wikidata.org/wiki/{id_value}"

    def _fetch_entity_by_id(self, entity_id) -> dict:
        api_url = f"https://www.wikidata.org/w/rest.php/wikibase/v1/entities/items/{entity_id}"
        return BasicDownloader(api_url).download().json()

    @staticmethod
    def _normalize_entity(entity_data: dict) -> dict:
        """Flatten a REST v1 payload, keeping best-rank statement values only

        Statements are reduced to {property_id: [content, ...]}. Statements
        without a value (novalue/somevalue) and deprecated ones are dropped; a
        property holding any preferred-rank statement keeps only those.
        """
        statements: dict[str, list] = {}
        for property_id, raw in (entity_data.get("statements") or {}).items():
            values = []
            preferred = []
            for statement in raw:
                value = statement.get("value")
                rank = statement.get("rank")
                if not isinstance(value, dict) or value.get("type") != "value":
                    continue
                content = value.get("content")
                if content is None or rank == "deprecated":
                    continue
                if rank == "preferred":
                    preferred.append(content)
                values.append(content)
            if values:
                statements[property_id] = preferred or values
        return {
            "id": entity_data.get("id"),
            "type": entity_data.get("type", "item"),
            "labels": entity_data.get("labels") or {},
            "descriptions": entity_data.get("descriptions") or {},
            "statements": statements,
        }

    def _extract_labels(self, entity_data: dict) -> dict[str, str]:
        """Extract labels only in preferred languages"""
        labels = entity_data["labels"]
        return {
            lang: labels[lang] for lang in WIKIDATA_PREFERRED_LANGS if lang in labels
        }

    def _extract_descriptions(self, entity_data: dict) -> list[dict[str, str]]:
        """Extract descriptions only in preferred languages"""
        descriptions = entity_data["descriptions"]
        return [
            {"lang": lang, "text": descriptions[lang]}
            for lang in WIKIDATA_PREFERRED_LANGS
            if lang in descriptions
        ]

    def _extract_property_values(self, entity_data: dict, property_id: str) -> list:
        """Extract all values of a property"""
        return entity_data["statements"].get(property_id, [])

    def _extract_property_value(
        self, entity_data: dict, property_id: str
    ) -> str | dict | None:
        """Extract the first value of a property"""
        values = self._extract_property_values(entity_data, property_id)
        return values[0] if values else None

    def _f_date(self, d: str) -> str:
        # Wikidata pads unknown parts with 00 ("2010-00-00" = year
        # precision); strip them to keep a partial ISO date
        while d.endswith("-00"):
            d = d[:-3]
        return d

    def _extract_date(self, entity_data: dict, property_id: str) -> str | None:
        """Extract a date from a time property"""
        value = self._extract_property_value(entity_data, property_id)
        if not isinstance(value, dict) or not isinstance(value.get("time"), str):
            return None
        # Wikidata time format: +YYYY-MM-DDTHH:MM:SSZ
        return self._f_date(value["time"].removeprefix("+").split("T")[0])

    def _extract_url(self, entity_data: dict, property_id: str) -> str | None:
        """Extract a URL from a url or string property"""
        value = self._extract_property_value(entity_data, property_id)
        return value if isinstance(value, str) else None

    # units of P2047 quantities -> factor to seconds
    _DURATION_UNIT_FACTORS = {
        "Q11574": 1,  # second
        "Q7727": 60,  # minute
        "Q25235": 3600,  # hour
    }

    def _extract_duration(self, entity_data: dict) -> int | None:
        """Extract duration in seconds from P2047"""
        value = self._extract_property_value(entity_data, WikidataProperties.DURATION)
        if not isinstance(value, dict) or "amount" not in value:
            return None
        # Wikidata stores duration as a quantity with a unit URI;
        # films are usually expressed in minutes
        unit = str(value.get("unit") or "").split("/")[-1]
        factor = self._DURATION_UNIT_FACTORS.get(unit, 60)
        return int(float(value["amount"]) * factor)

    def _extract_entity_types(self, entity_data: dict, property_id: str) -> list[str]:
        """Extract the QIDs of a wikibase-item property, e.g. P31 or P279"""
        return self._extract_property_values(entity_data, property_id)

    def _match_entity_types(self, entity_types: list[str]) -> type[Item] | None:
        """Map entity types to a model, letting priority types win over order"""
        for priority_type in self.PRIORITY_TYPES:
            if (
                priority_type in entity_types
                and priority_type in self.TYPE_TO_MODEL_MAP
            ):
                return self.TYPE_TO_MODEL_MAP[priority_type]

        for entity_type in entity_types:
            model = self.TYPE_TO_MODEL_MAP.get(entity_type)
            if model:
                return model

        return None

    def _match_ancestor_types(self, entity_types: list[str]) -> type[Item] | None:
        """Map ancestor types, ignoring the ones too generic to tell a category"""
        return self._match_entity_types(
            [t for t in entity_types if t not in self.AMBIGUOUS_ANCESTOR_TYPES]
        )

    def _fetch_parent_types(self, class_id: str) -> list[str]:
        """Fetch the 'subclass of' values of a class item

        A class that cannot be fetched is skipped rather than fatal, and is not
        cached, so a later import may still resolve it.
        """
        cached = _PARENT_TYPE_CACHE.get(class_id)
        if cached is not None:
            return cached

        try:
            entity_data = self._fetch_entity_by_id(class_id)
        except DownloadError as e:
            logger.warning(f"Unable to fetch Wikidata class {class_id}: {e}")
            return []
        if not entity_data:
            return []

        parent_types = self._extract_entity_types(
            self._normalize_entity(entity_data), WikidataProperties.SUBCLASS_OF
        )
        _PARENT_TYPE_CACHE[class_id] = parent_types
        return parent_types

    def _walk_ancestor_types(
        self, instance_of: list[str], subclass_of: list[str]
    ) -> type[Item] | None:
        """Find the nearest mapped ancestor, walking 'subclass of' level by level

        The entity's own 'subclass of' values are the first candidates; its
        'instance of' values only seed the walk, having already failed to match.
        The nearest level wins, and within a level the priority types do.
        """
        model = self._match_ancestor_types(subclass_of)
        if model:
            return model

        frontier = subclass_of + [t for t in instance_of if t not in subclass_of]
        visited = set(frontier)
        for _ in range(self.MAX_DEPTH):
            parent_types: list[str] = []
            for class_id in frontier:
                for parent_type in self._fetch_parent_types(class_id):
                    if parent_type not in visited:
                        visited.add(parent_type)
                        parent_types.append(parent_type)
            if not parent_types:
                return None
            model = self._match_ancestor_types(parent_types)
            if model:
                return model
            frontier = parent_types

        return None

    def _determine_entity_type(self, entity_data: dict) -> type[Item]:
        """Determine the model for an entity from its type properties

        Direct 'instance of' (P31) values decide first, ambiguous ones included;
        failing that, the subclass graph above the entity is walked up to its
        nearest mapped ancestor.
        """
        instance_of = self._extract_entity_types(
            entity_data, WikidataProperties.INSTANCE_OF
        )
        if not instance_of:
            raise ParseError(
                self, f"Entity {self.id_value} has no 'instance of' (P31) properties"
            )

        model = self._match_entity_types(instance_of) or self._walk_ancestor_types(
            instance_of,
            self._extract_entity_types(entity_data, WikidataProperties.SUBCLASS_OF),
        )
        if model:
            return model

        logger.error(
            f"Entity has unsupported type(s): {', '.join(instance_of)}",
            extra={"qid": self.id_value},
        )
        raise ParseError(
            self,
            f"Entity has unsupported type(s): {', '.join(instance_of)}",
        )

    def _extract_cover_image(self, entity_data: dict) -> str | None:
        """Extract cover image URL from P18 (image) property"""
        filename = self._extract_property_value(entity_data, WikidataProperties.IMAGE)
        if not isinstance(filename, str):
            return None

        # Special:FilePath redirects to the Commons image at the requested width
        return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(filename)}?width=1000"

    def _fallback_label(self, entity_data: dict) -> dict[str, str]:
        """Pick one label outside the preferred languages, English first"""
        labels = entity_data["labels"]
        lang = "en" if "en" in labels else next(iter(labels), None)
        return {lang: labels[lang]} if lang else {}

    def scrape(self) -> ResourceContent:
        if not self.id_value or not self.id_value.startswith("Q"):
            raise ParseError(self, "QID")
        entity_data = self._fetch_entity_by_id(self.id_value)

        if not isinstance(entity_data, dict) or not entity_data.get("id"):
            raise ParseError(self, "json")
        if entity_data["id"] != self.id_value:
            # a merged QID redirects to the item it was merged into; that
            # payload is what this QID means now
            logger.info(f"{self.id_value} redirected to {entity_data['id']}")
        entity_data = self._normalize_entity(entity_data)

        # Extract labels (titles)
        labels = self._extract_labels(entity_data) or self._fallback_label(entity_data)
        title = next(iter(labels.values()), self.id_value)

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
        data.metadata["preferred_model"] = model.__name__

        # Extract model-specific metadata
        extractor = {
            Album: self._extract_album_metadata,
            Game: self._extract_game_metadata,
            Podcast: self._extract_podcast_metadata,
            PodcastEpisode: self._extract_podcast_episode_metadata,
            Performance: self._extract_performance_metadata,
            Movie: self._extract_movie_metadata,
            TVShow: self._extract_tv_show_metadata,
            TVSeason: self._extract_tv_season_metadata,
            TVEpisode: self._extract_tv_episode_metadata,
            Work: self._extract_work_metadata,
            People: self._extract_people_metadata,
        }.get(model)
        if extractor:
            extractor(entity_data, data)

        for res in self._extract_external_ids(entity_data):
            try:
                site_cls = SiteManager.get_site_cls_by_id_type(res["id_type"])
            except ValueError:
                # No registered site for this IdType; still store the lookup ID
                data.lookup_ids[res["id_type"]] = res["id_value"]
                continue
            if model == site_cls.DEFAULT_MODEL or model in site_cls.MATCHABLE_MODELS:
                data.lookup_ids[res["id_type"]] = res["id_value"]
            else:
                logger.warning(
                    f"IdType {res['id_type']} does not match Model {model}, skipping",
                    extra={
                        "id_type": self.ID_TYPE,
                        "id_value": self.id_value,
                        "resource": res,
                    },
                )
        return data

    # P31 class -> Album.album_type slug (ALBUM_TYPE_CATALOG)
    _ALBUM_TYPE_BY_CLASS = {
        WikidataTypes.MUSIC_ALBUM: "album",
        WikidataTypes.MUSIC_SINGLE: "single",
        WikidataTypes.MUSIC_EP: "ep",
        "Q208569": "album",  # studio album
        "Q209939": "live",  # live album
        "Q222910": "compilation",  # compilation album
        "Q723849": "compilation",  # greatest hits album
        "Q4176708": "soundtrack",  # soundtrack album
        "Q1892995": "mixtape",  # mixtape
        "Q963099": "remix",  # remix album
        "Q220935": "demo",  # demo
        "Q107154516": "ep",  # mini album
        "Q106042566": "single",  # single album
    }

    def _extract_album_metadata(self, entity_data, data):
        """Extract Album-specific metadata"""
        data.metadata["release_date"] = self._extract_date(
            entity_data, WikidataProperties.PUBLICATION_DATE
        )
        data.metadata["length"] = self._extract_duration(entity_data)
        types = self._extract_entity_types(entity_data, WikidataProperties.INSTANCE_OF)
        data.metadata["album_type"] = list(
            dict.fromkeys(
                self._ALBUM_TYPE_BY_CLASS[t]
                for t in types
                if t in self._ALBUM_TYPE_BY_CLASS
            )
        )
        # performer, record label and genre are entity references,
        # unusable until labels are resolved
        data.metadata["artist"] = []

    def _extract_game_metadata(self, entity_data, data):
        """Extract Game-specific metadata"""
        data.metadata["release_date"] = self._extract_date(
            entity_data, WikidataProperties.PUBLICATION_DATE
        )
        data.metadata["artist"] = []  # No direct Wikidata property for artist
        data.metadata["official_site"] = self._extract_url(
            entity_data, WikidataProperties.OFFICIAL_WEBSITE
        )

    def _extract_podcast_metadata(self, entity_data, data):
        """Extract Podcast-specific metadata"""
        data.metadata["official_site"] = self._extract_url(
            entity_data, WikidataProperties.OFFICIAL_WEBSITE
        )

        # RSS feed URL
        feed_url = self._extract_url(
            entity_data, WikidataProperties.WORK_AVAILABLE_AT_URL
        )
        if feed_url:
            data.lookup_ids[IdType.RSS] = feed_url

    def _extract_podcast_episode_metadata(self, entity_data, data):
        """Extract PodcastEpisode-specific metadata"""
        data.metadata["pub_date"] = self._extract_date(
            entity_data, WikidataProperties.PUBLICATION_DATE
        )
        data.metadata["length"] = self._extract_duration(entity_data)
        data.metadata["guid"] = self._extract_property_value(
            entity_data, WikidataProperties.ISSUE_NUMBER
        )
        data.metadata["media_url"] = self._extract_url(
            entity_data, WikidataProperties.WORK_AVAILABLE_AT_URL
        )
        data.metadata["link"] = self._extract_url(
            entity_data, WikidataProperties.OFFICIAL_WEBSITE
        )

    def _extract_performance_metadata(self, entity_data, data):
        """Extract Performance-specific metadata"""
        data.metadata["opening_date"] = self._extract_date(
            entity_data, WikidataProperties.PUBLICATION_DATE
        )
        data.metadata["closing_date"] = self._extract_date(
            entity_data, WikidataProperties.END_TIME
        )
        data.metadata["official_site"] = self._extract_url(
            entity_data, WikidataProperties.OFFICIAL_WEBSITE
        )
        data.metadata["crew"] = []

    def _extract_movie_metadata(self, entity_data, data):
        """Extract Movie-specific metadata"""
        data.metadata["release_date"] = self._extract_date(
            entity_data, WikidataProperties.PUBLICATION_DATE
        )

    def _extract_tv_show_metadata(self, entity_data, data):
        """Extract TVShow-specific metadata"""
        data.metadata["first_air_date"] = self._extract_date(
            entity_data, WikidataProperties.PUBLICATION_DATE
        )
        data.metadata["last_air_date"] = self._extract_date(
            entity_data, WikidataProperties.END_TIME
        )
        data.metadata["number_of_episodes"] = self._extract_property_value(
            entity_data, WikidataProperties.NUMBER_OF_EPISODES
        )
        data.metadata["number_of_seasons"] = self._extract_property_value(
            entity_data, WikidataProperties.NUMBER_OF_SEASONS
        )

    def _extract_tv_season_metadata(self, entity_data, data):
        """Extract TVSeason-specific metadata"""
        data.metadata["first_air_date"] = self._extract_date(
            entity_data, WikidataProperties.PUBLICATION_DATE
        )
        data.metadata["last_air_date"] = self._extract_date(
            entity_data, WikidataProperties.END_TIME
        )
        data.metadata["number_of_episodes"] = self._extract_property_value(
            entity_data, WikidataProperties.NUMBER_OF_EPISODES
        )

    def _extract_tv_episode_metadata(self, entity_data, data):
        """Extract TVEpisode-specific metadata"""
        data.metadata["air_date"] = self._extract_date(
            entity_data, WikidataProperties.PUBLICATION_DATE
        )
        data.metadata["episode_number"] = self._extract_property_value(
            entity_data, WikidataProperties.ISSUE_NUMBER
        )
        data.metadata["length"] = self._extract_duration(entity_data)

    def _extract_work_metadata(self, entity_data, data):
        """Extract Work (Book/Literary work)-specific metadata"""
        data.metadata["publication_date"] = self._extract_date(
            entity_data, WikidataProperties.PUBLICATION_DATE
        )

    _ORGANIZATION_TYPES = {
        WikidataTypes.BUSINESS_ENTERPRISE,
        WikidataTypes.PUBLISHER,
        WikidataTypes.RECORD_LABEL,
        WikidataTypes.FILM_PRODUCTION_COMPANY,
        WikidataTypes.VIDEO_GAME_DEVELOPER,
        WikidataTypes.VIDEO_GAME_PUBLISHER,
        WikidataTypes.ANIMATION_STUDIO,
        WikidataTypes.FILM_STUDIO,
        WikidataTypes.THEATER_COMPANY,
    }

    def _extract_people_metadata(self, entity_data, data):
        """Extract People-specific metadata"""
        # People uses localized_name/localized_bio instead of localized_title/localized_description
        data.metadata["localized_name"] = data.metadata.pop("localized_title", [])
        data.metadata["localized_bio"] = data.metadata.pop("localized_description", [])
        # Determine people_type from entity types
        instance_types = set(
            self._extract_entity_types(entity_data, WikidataProperties.INSTANCE_OF)
        )
        if instance_types & self._ORGANIZATION_TYPES:
            data.metadata["people_type"] = "organization"
        data.metadata["birth_date"] = self._extract_date(
            entity_data, WikidataProperties.DATE_OF_BIRTH
        )
        data.metadata["death_date"] = self._extract_date(
            entity_data, WikidataProperties.DATE_OF_DEATH
        )
        data.metadata["official_site"] = self._extract_url(
            entity_data, WikidataProperties.OFFICIAL_WEBSITE
        )

    def get_wikipedia_pages(self) -> list[dict[str, str]]:
        """Fetch all Wikipedia pages for this Wikidata entity

        Returns one {"lang", "url", "title"} dict per Wikipedia sitelink.

        Example: [
            {"lang": "en", "url": "https://en.wikipedia.org/wiki/The_Matrix",
             "title": "The Matrix"},
            ...
        ]
        """
        entity_id = self.id_value
        if not entity_id:
            return []

        try:
            # Use Wikidata API to get all sitelinks (Wikipedia pages)
            api_url = f"https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props=sitelinks&ids={entity_id}"

            response = BasicDownloader(api_url, timeout=2).download()
            data = response.json()

            if "entities" not in data or entity_id not in data["entities"]:
                logger.warning(f"No entity data found for {entity_id}")
                return []

            entity = data["entities"][entity_id]
            if "sitelinks" not in entity:
                logger.warning(f"No sitelinks found for {entity_id}")
                return []

            # Extract Wikipedia pages
            wiki_pages = []
            for site_key, site_data in entity["sitelinks"].items():
                # Only include Wikipedia links (skip other projects like Wiktionary)
                if site_key.endswith("wiki") and not site_key.startswith("commons"):
                    lang_code = site_key.replace("wiki", "")
                    title = site_data["title"]
                    url = f"https://{lang_code}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
                    wiki_pages.append({"lang": lang_code, "url": url, "title": title})

            return wiki_pages

        except Exception as e:
            logger.error(
                f"Error fetching Wikipedia pages: {e}",
                extra={"QID": entity_id, "exception": e},
            )
            return []

    def _extract_external_ids(self, entity_data: dict) -> list[dict]:
        """Extract common external identifiers to lookup_ids"""
        resources = []
        for property_id, id_type in WikidataProperties.IdTypeMapping.items():
            value = self._extract_property_value(entity_data, property_id)
            if not value:
                continue
            if id_type in [IdType.OpenLibrary, IdType.OpenLibrary_Work]:
                id_type = OpenLibrary.guess_id_type(value)
            resources.append({"id_type": id_type, "id_value": value})
        return resources

    @staticmethod
    def _escape_sparql_string(value: str) -> str:
        """Escape a value for use inside a double-quoted SPARQL literal"""
        return value.replace("\\", "\\\\").replace('"', '\\"')

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
        # OpenLibrary Edition / Work / Author all share property P648
        if id_type in (
            IdType.OpenLibrary,
            IdType.OpenLibrary_Work,
            IdType.OpenLibrary_Author,
        ):
            property_id = "P648"
        else:
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
                ?item wdt:{property_id} "{cls._escape_sparql_string(id_value)}".
            }}
            LIMIT 1
            """

            api_url = "https://query.wikidata.org/sparql"
            params = {"query": sparql_query, "format": "json"}
            full_url = f"{api_url}?{urlencode(params)}"

            response = BasicDownloader(full_url).download()
            data = response.json()

            # Extract QID from results
            if data.get("results", {}).get("bindings"):
                item_uri = data["results"]["bindings"][0]["item"]["value"]
                # Extract QID from URI (e.g., "http://www.wikidata.org/entity/Q12345" -> "Q12345")
                qid = item_uri.split("/")[-1]
                return qid
            else:
                return None

        except Exception as e:
            logger.error(f"Failed to lookup Wikidata QID for {id_type}:{id_value}: {e}")
            return None
