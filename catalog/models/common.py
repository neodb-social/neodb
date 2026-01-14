from django.db import models
from django.utils.translation import gettext_lazy as _
from ninja import Schema

from common.models import LANGUAGE_CHOICES, LOCALE_CHOICES, SCRIPT_CHOICES, jsondata


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
    # DoubanBook_Author = "doubanbook_author", _("Douban Book Author")
    # DoubanCelebrity = "doubanmovie_celebrity", _("Douban Movie Celebrity")
    # Goodreads_Author = "goodreads_author", _("Goodreads Author")
    # Spotify_Artist = "spotify_artist", _("Spotify Artist")
    # TMDB_Person = "tmdb_person", _("TMDB Person")
    IGDB = "igdb", _("IGDB Game")
    BGG = "bgg", _("BGG Boardgame")
    Steam = "steam", _("Steam Game")
    Itch = "itch", _("itch.io")
    Bangumi = "bangumi", _("Bangumi")
    ApplePodcast = "apple_podcast", _("Apple Podcast")
    AppleMusic = "apple_music", _("Apple Music")
    Fediverse = "fedi", _("Fediverse")
    Qidian = "qidian", _("Qidian")
    Ypshuo = "ypshuo", _("Ypshuo")
    AO3 = "ao3", _("Archive of Our Own")
    JJWXC = "jjwxc", _("JinJiang")
    OpenLibrary = "openlibrary", _("Open Library")
    OpenLibrary_Work = "openlibrary_work", _("Open Library Work")


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
    # People = "people", _("Person / Organization")


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
    # People = "people", _("Person / Organization")
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
