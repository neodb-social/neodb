from functools import lru_cache

from django.db import models
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from ninja import Schema

from common.models.duration import duration_to_seconds, format_duration

from common.models import (
    ALBUM_TYPE_CHOICES,
    COUNTRY_CHOICES,
    GAME_PLATFORM_CHOICES,
    LANGUAGE_CHOICES,
    LOCALE_CHOICES,
    MEDIA_FORMAT_CHOICES,
    SCRIPT_CHOICES,
    country_display_name,
    genre_choices_for,
    jsondata,
)


class SiteName(models.TextChoices):
    Unknown = "unknown", _("Unknown")
    Douban = "douban", _("Douban")
    Goodreads = "goodreads", _("Goodreads")
    GoogleBooks = "googlebooks", _("Google Books")
    BooksTW = "bookstw", _("BooksTW")
    BibliotekDK = "bibliotekdk", _("Bibliotek.dk")
    BibliotekDK_eReolen = "eReolen", _("eReolen.dk")
    IMDB = "imdb", _("IMDb")
    TMDB = "tmdb", _("TMDB")
    Bandcamp = "bandcamp", _("Bandcamp")
    Spotify = "spotify", _("Spotify")
    IGDB = "igdb", _("IGDB")
    Steam = "steam", _("Steam")
    Itch = "itch", _("itch.io")
    Bangumi = "bangumi", _("Bangumi")
    BGG = "bgg", _("BGG")
    ApplePodcast = "apple_podcast", _("Apple Podcast")
    RSS = "rss", _("RSS")
    Discogs = "discogs", _("Discogs")
    AppleMusic = "apple_music", _("Apple Music")
    Fediverse = "fedi", _("Fediverse")
    Qidian = "qidian", _("Qidian")
    Ypshuo = "ypshuo", _("Ypshuo")
    AO3 = "ao3", _("Archive of Our Own")
    JJWXC = "jjwxc", _("JinJiang")
    WikiData = "wikidata", _("WikiData")
    OpenLibrary = "openlibrary", _("Open Library")
    MusicBrainz = "musicbrainz", _("MusicBrainz")
    WorldCat = "worldcat", _("WorldCat")
    MobyGames = "mobygames", _("MobyGames")
    StoryGraph = "storygraph", _("StoryGraph")
    YouTubeMusic = "yt_music", _("YouTube Music")
    RateYourMusic = "rateyourmusic", _("RateYourMusic")


class IdType(models.TextChoices):  # values must be in lowercase
    WikiData = "wikidata", _("WikiData")
    ISBN10 = "isbn10", _("ISBN10")
    ISBN = "isbn", _("ISBN")  # ISBN 13
    ASIN = "asin", _("ASIN")
    ISSN = "issn", _("ISSN")
    CUBN = "cubn", _("CUBN")
    ISRC = "isrc", _("ISRC")  # only for songs
    GTIN = ("gtin", _("GTIN UPC EAN"))  # GTIN-13, ISBN is separate
    OCLC = "oclc", _("OCLC Number")
    RSS = "rss", _("RSS Feed URL")
    IMDB = "imdb", _("IMDb")
    TMDB_TV = "tmdb_tv", _("TMDB TV Series")
    TMDB_TVSeason = "tmdb_tvseason", _("TMDB TV Season")
    TMDB_TVEpisode = "tmdb_tvepisode", _("TMDB TV Episode")
    TMDB_Movie = "tmdb_movie", _("TMDB Movie")
    Goodreads = "goodreads", _("Goodreads")
    Goodreads_Work = "goodreads_work", _("Goodreads Work")
    GoogleBooks = "googlebooks", _("Google Books")
    DoubanBook = "doubanbook", _("Douban Book")
    DoubanBook_Work = "doubanbook_work", _("Douban Book Work")
    DoubanMovie = "doubanmovie", _("Douban Movie")
    DoubanMusic = "doubanmusic", _("Douban Music")
    DoubanGame = "doubangame", _("Douban Game")
    DoubanDrama = "doubandrama", _("Douban Drama")
    DoubanDramaVersion = "doubandrama_version", _("Douban Drama Version")
    BooksTW = "bookstw", _("BooksTW Book")
    BibliotekDK_Edition = "bibliotekdk_edition", _("Bibliotek.dk")
    BibliotekDK_eReolen = "bibliotekdk_ereolen", _("eReolen.dk")
    BibliotekDK_Work = "bibliotekdk_work", _("Bibliotek.dk")
    Bandcamp = "bandcamp", _("Bandcamp")
    Spotify_Album = "spotify_album", _("Spotify Album")
    Spotify_Show = "spotify_show", _("Spotify Podcast")
    Discogs_Release = "discogs_release", _("Discogs Release")
    Discogs_Master = "discogs_master", _("Discogs Master")
    MusicBrainz_ReleaseGroup = (
        "musicbrainz_releasegroup",
        _("MusicBrainz Release Group"),
    )
    MusicBrainz_Release = "musicbrainz_release", _("MusicBrainz Release")
    MusicBrainz_Artist = "musicbrainz_artist", _("MusicBrainz Artist")
    DoubanPersonage = "douban_personage", _("Douban Personage")
    Goodreads_Author = "goodreads_author", _("Goodreads Author")
    Spotify_Artist = "spotify_artist", _("Spotify Artist")
    TMDB_Person = "tmdb_person", _("TMDB Person")
    OpenLibrary_Author = "openlibrary_author", _("Open Library Author")
    IGDB_Company = "igdb_company", _("IGDB Company")
    IGDB = "igdb", _("IGDB Game")
    BGG = "bgg", _("BGG Boardgame")
    Steam = "steam", _("Steam Game")
    Itch = "itch", _("itch.io")
    Bangumi = "bangumi", _("Bangumi")
    ApplePodcast = "apple_podcast", _("Apple Podcast")
    AppleMusic = "apple_music", _("Apple Music")
    YouTubeMusic = "yt_music", _("YouTube Music")
    Fediverse = "fedi", _("Fediverse")
    Qidian = "qidian", _("Qidian")
    Ypshuo = "ypshuo", _("Ypshuo")
    AO3 = "ao3", _("Archive of Our Own")
    JJWXC = "jjwxc", _("JinJiang")
    OpenLibrary = "openlibrary", _("Open Library")
    OpenLibrary_Work = "openlibrary_work", _("Open Library Work")
    MobyGames = "mobygames", _("MobyGames")
    StoryGraph = "storygraph", _("StoryGraph")
    RateYourMusic_Release = "rateyourmusic_release", _("RateYourMusic Release")


IdealIdTypes = [
    IdType.ISBN,
    IdType.CUBN,
    IdType.ASIN,
    IdType.GTIN,
    IdType.ISRC,
    IdType.OCLC,
    IdType.MusicBrainz_ReleaseGroup,
    IdType.RSS,
    IdType.IMDB,
    IdType.Steam,
    IdType.Itch,
    IdType.WikiData,
    IdType.TMDB_Person,
]


class ItemType(models.TextChoices):
    Edition = "edition", _("Edition")
    Work = "work", _("Work")
    TVShow = "tvshow", _("TV Series")
    TVSeason = "tvseason", _("TV Season")
    TVEpisode = "tvepisode", _("TV Episode")
    Movie = "movie", _("Movie")
    Album = "music", _("Album")
    Game = "game", _("Game")
    Podcast = "podcast", _("Podcast Program")
    PodcastEpisode = "podcastepisode", _("Podcast Episode")
    Performance = "performance", _("Performance")
    PerformanceProduction = "production", _("Production")
    Exhibition = "exhibition", _("Exhibition")
    Collection = "collection", _("Collection")
    # Person = "person", _("Person")
    # Organization = "organization", _("Organization")
    People = "people", _("Person / Organization")


class ItemCategory(models.TextChoices):
    Book = "book", _("Book")
    Movie = "movie", _("Movie")
    TV = "tv", _("TV")
    Music = "music", _("Music")
    Game = "game", _("Game")
    Podcast = "podcast", _("Podcast")
    Performance = "performance", _("Performance")
    # FanFic = "fanfic", _("FanFic")
    # Exhibition = "exhibition", _("Exhibition")
    People = "people", _("Person / Organization")
    Collection = "collection", _("Collection")


class AvailableItemCategory(models.TextChoices):
    Book = "book", _("Book")
    Movie = "movie", _("Movie")
    TV = "tv", _("TV")
    Music = "music", _("Music")
    Game = "game", _("Game")
    Podcast = "podcast", _("Podcast")
    Performance = "performance", _("Performance")


# class SubItemType(models.TextChoices):
#     Season = "season", _("season")
#     Episode = "episode", _("episode")
#     Version = "production", _("production")


class LocalizedLabelSchema(Schema):
    lang: str
    text: str


class VideoFieldsResolverMixin(Schema):
    """Shared resolvers for origin_country / release_date / length and
    the deprecated aliases (area, showtime, duration) on Movie and TV
    schemas. duration keeps a display-string shape for older peers and
    clients; length carries the canonical seconds."""

    @staticmethod
    def resolve_origin_country(obj) -> list[str]:
        return obj.origin_country or []

    @staticmethod
    def resolve_area(obj) -> list[str]:
        return obj.origin_country or []

    @staticmethod
    def resolve_showtime(obj) -> list[dict]:
        return [{"time": obj.release_date, "region": ""}] if obj.release_date else []

    @staticmethod
    def resolve_length(obj) -> int | None:
        # tolerate legacy free-text values not yet migrated; numeric
        # values are trusted as seconds
        return duration_to_seconds(obj.length)

    @staticmethod
    def resolve_duration(obj) -> str | None:
        seconds = duration_to_seconds(obj.length)
        return format_duration(seconds) if seconds else None


def get_locale_choices_for_jsonform(choices, const=False):
    """return list for jsonform schema"""
    return [{"title": v, "const" if const else "value": k} for k, v in choices]


LOCALE_CHOICES_JSONFORM = get_locale_choices_for_jsonform(LOCALE_CHOICES)
LANGUAGE_CHOICES_JSONFORM = get_locale_choices_for_jsonform(
    LANGUAGE_CHOICES, const=True
)
SCRIPT_CHOICES_JSONFORM = get_locale_choices_for_jsonform(SCRIPT_CHOICES, const=True)

LOCALIZED_LABEL_SCHEMA = {
    "type": "list",
    "items": {
        "type": "dict",
        "keys": {
            "lang": {
                "type": "string",
                "title": _("locale"),
                "choices": LOCALE_CHOICES_JSONFORM,
            },
            "text": {"type": "string", "title": _("text content")},
        },
        "required": ["lang", "text"],
    },
    "minItems": 1,
    "uniqueItems": True,
}

LOCALIZED_DESCRIPTION_SCHEMA = {
    "type": "list",
    "items": {
        "type": "dict",
        "keys": {
            "lang": {
                "type": "string",
                "title": _("locale"),
                "choices": LOCALE_CHOICES_JSONFORM,
            },
            "text": {
                "type": "string",
                "title": _("text content"),
                "widget": "textarea",
            },
        },
        "required": ["lang", "text"],
    },
    "uniqueItems": True,
}

LIST_OF_STR_SCHEMA = {
    "type": "list",
    "items": {"type": "string", "required": True},
    "uniqueItems": True,
}

LIST_OF_ONE_PLUS_STR_SCHEMA = {
    "type": "list",
    "items": {"type": "string", "required": True},
    "minItems": 1,
    "uniqueItems": True,
}


def GenreListField(category=None):
    """ArrayField whose dropdown offers only the genres relevant to `category`.

    `category` is an ItemCategory (or its value); None offers the full catalog.
    The schema is a callable so the dropdown reflects the live per-category
    config from the admin UI (see common.models.genre.genre_choices_for) without
    a restart -- django_jsonform resolves callable schemas at render time.
    """

    def schema():
        choices = get_locale_choices_for_jsonform(
            genre_choices_for(category), const=True
        )
        return {
            "type": "array",
            "items": {"oneOf": choices + [{"title": "Other", "type": "string"}]},
            "uniqueItems": True,
        }

    return jsondata.ArrayField(
        verbose_name=_("genre"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
        schema=schema,
    )


@lru_cache(maxsize=None)
def _country_jsonform_schema(lang: str) -> dict:
    # ~250 display-name lookups + a sort; cache per UI language since the
    # result is identical for every render in that language
    choices = get_locale_choices_for_jsonform(
        sorted(
            ((code, country_display_name(code)) for code, _ in COUNTRY_CHOICES),
            key=lambda x: x[1],
        ),
        const=True,
    )
    return {
        "type": "array",
        "items": {"oneOf": choices + [{"title": "Other", "type": "string"}]},
        "uniqueItems": True,
    }


def CountryListField():
    """ArrayField of ISO 3166-1 alpha-2 codes with an "Other" passthrough.

    The schema is a callable so the dropdown labels follow the active UI
    language at render time (django_jsonform resolves callable schemas).
    """

    def schema():
        return _country_jsonform_schema(get_language() or "en")

    return jsondata.ArrayField(
        verbose_name=_("origin country"),
        base_field=models.CharField(blank=True, default="", max_length=100),
        null=True,
        blank=True,
        default=list,
        schema=schema,
    )


def _slug_list_field(verbose_name, choices):
    def schema():
        options = get_locale_choices_for_jsonform(choices, const=True)
        return {
            "type": "array",
            "items": {"oneOf": options + [{"title": "Other", "type": "string"}]},
            "uniqueItems": True,
        }

    return jsondata.ArrayField(
        verbose_name=verbose_name,
        base_field=models.CharField(blank=True, default="", max_length=100),
        null=True,
        blank=True,
        default=list,
        schema=schema,
    )


def AlbumTypeListField():
    return _slug_list_field(_("album type"), ALBUM_TYPE_CHOICES)


def GamePlatformListField():
    return _slug_list_field(_("platform"), GAME_PLATFORM_CHOICES)


def MediaFormatListField():
    return _slug_list_field(_("media format"), MEDIA_FORMAT_CHOICES)


def LanguageListField(script=False):
    return jsondata.ArrayField(
        verbose_name=_("language"),
        base_field=models.CharField(blank=True, default="", max_length=100),
        null=True,
        blank=True,
        default=list,
        schema={
            "type": "array",
            "items": {
                "oneOf": (
                    SCRIPT_CHOICES_JSONFORM if script else LANGUAGE_CHOICES_JSONFORM
                )
                + [{"title": "Other", "type": "string"}]
            },
            "uniqueItems": True,
        },
    )
