import re
import uuid
from functools import cached_property
from typing import TYPE_CHECKING, Any, Self

from auditlog.context import disable_auditlog
from auditlog.models import LogEntry
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.signing import b62_decode, b62_encode
from django.db import connection, models
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from loguru import logger
from ninja import Field, Schema
from polymorphic.models import PolymorphicModel

from catalog.common import jsondata
from common.models import LANGUAGE_CHOICES, LOCALE_CHOICES, get_current_locales, uniq

from .utils import item_cover_path, resource_cover_path

if TYPE_CHECKING:
    from journal.models import Collection
    from users.models import User

    from .sites import ResourceContent


class SiteName(models.TextChoices):
    Unknown = "unknown", _("Unknown")  # type:ignore[reportCallIssue]
    Douban = "douban", _("Douban")  # type:ignore[reportCallIssue]
    Goodreads = "goodreads", _("Goodreads")  # type:ignore[reportCallIssue]
    GoogleBooks = "googlebooks", _("Google Books")  # type:ignore[reportCallIssue]
    BooksTW = "bookstw", _("BooksTW")  # type:ignore[reportCallIssue]
    IMDB = "imdb", _("IMDb")  # type:ignore[reportCallIssue]
    TMDB = "tmdb", _("TMDB")  # type:ignore[reportCallIssue]
    Bandcamp = "bandcamp", _("Bandcamp")  # type:ignore[reportCallIssue]
    Spotify = "spotify", _("Spotify")  # type:ignore[reportCallIssue]
    IGDB = "igdb", _("IGDB")  # type:ignore[reportCallIssue]
    Steam = "steam", _("Steam")  # type:ignore[reportCallIssue]
    Bangumi = "bangumi", _("Bangumi")  # type:ignore[reportCallIssue]
    BGG = "bgg", _("BGG")  # type:ignore[reportCallIssue]
    ApplePodcast = "apple_podcast", _("Apple Podcast")  # type:ignore[reportCallIssue]
    RSS = "rss", _("RSS")  # type:ignore[reportCallIssue]
    Discogs = "discogs", _("Discogs")  # type:ignore[reportCallIssue]
    AppleMusic = "apple_music", _("Apple Music")  # type:ignore[reportCallIssue]
    Fediverse = "fedi", _("Fediverse")  # type:ignore[reportCallIssue]
    Qidian = "qidian", _("Qidian")  # type:ignore[reportCallIssue]
    Ypshuo = "ypshuo", _("Ypshuo")  # type:ignore[reportCallIssue]
    AO3 = "ao3", _("Archive of Our Own")  # type:ignore[reportCallIssue]
    JJWXC = "jjwxc", _("JinJiang")  # type:ignore[reportCallIssue]


class IdType(models.TextChoices):
    WikiData = "wikidata", _("WikiData")  # type:ignore[reportCallIssue]
    ISBN10 = "isbn10", _("ISBN10")  # type:ignore[reportCallIssue]
    ISBN = "isbn", _("ISBN")  # ISBN 13  # type:ignore[reportCallIssue]
    ASIN = "asin", _("ASIN")  # type:ignore[reportCallIssue]
    ISSN = "issn", _("ISSN")  # type:ignore[reportCallIssue]
    CUBN = "cubn", _("CUBN")  # type:ignore[reportCallIssue]
    ISRC = "isrc", _("ISRC")  # only for songs  # type:ignore[reportCallIssue]
    GTIN = (
        "gtin",
        _("GTIN UPC EAN"),
    )  # GTIN-13, ISBN is separate  # type:ignore[reportCallIssue]
    RSS = "rss", _("RSS Feed URL")  # type:ignore[reportCallIssue]
    IMDB = "imdb", _("IMDb")  # type:ignore[reportCallIssue]
    TMDB_TV = "tmdb_tv", _("TMDB TV Serie")  # type:ignore[reportCallIssue]
    TMDB_TVSeason = "tmdb_tvseason", _("TMDB TV Season")  # type:ignore[reportCallIssue]
    TMDB_TVEpisode = "tmdb_tvepisode", _("TMDB TV Episode")  # type:ignore[reportCallIssue]
    TMDB_Movie = "tmdb_movie", _("TMDB Movie")  # type:ignore[reportCallIssue]
    Goodreads = "goodreads", _("Goodreads")  # type:ignore[reportCallIssue]
    Goodreads_Work = "goodreads_work", _("Goodreads Work")  # type:ignore[reportCallIssue]
    Goodreads_Series = "goodreads_series", _("Goodreads Series")  # type:ignore[reportCallIssue]
    GoogleBooks = "googlebooks", _("Google Books")  # type:ignore[reportCallIssue]
    DoubanBook = "doubanbook", _("Douban Book")  # type:ignore[reportCallIssue]
    DoubanBook_Work = "doubanbook_work", _("Douban Book Work")  # type:ignore[reportCallIssue]
    DoubanMovie = "doubanmovie", _("Douban Movie")  # type:ignore[reportCallIssue]
    DoubanMusic = "doubanmusic", _("Douban Music")  # type:ignore[reportCallIssue]
    DoubanGame = "doubangame", _("Douban Game")  # type:ignore[reportCallIssue]
    DoubanDrama = "doubandrama", _("Douban Drama")  # type:ignore[reportCallIssue]
    DoubanDramaVersion = "doubandrama_version", _("Douban Drama Version")  # type:ignore[reportCallIssue]
    BooksTW = "bookstw", _("BooksTW Book")  # type:ignore[reportCallIssue]
    Bandcamp = "bandcamp", _("Bandcamp")  # type:ignore[reportCallIssue]
    Spotify_Album = "spotify_album", _("Spotify Album")  # type:ignore[reportCallIssue]
    Spotify_Show = "spotify_show", _("Spotify Podcast")  # type:ignore[reportCallIssue]
    Discogs_Release = "discogs_release", _("Discogs Release")  # type:ignore[reportCallIssue]
    Discogs_Master = "discogs_master", _("Discogs Master")  # type:ignore[reportCallIssue]
    MusicBrainz = "musicbrainz", _("MusicBrainz ID")  # type:ignore[reportCallIssue]
    # DoubanBook_Author = "doubanbook_author", _("Douban Book Author")  # type:ignore[reportCallIssue]
    # DoubanCelebrity = "doubanmovie_celebrity", _("Douban Movie Celebrity")  # type:ignore[reportCallIssue]
    # Goodreads_Author = "goodreads_author", _("Goodreads Author")  # type:ignore[reportCallIssue]
    # Spotify_Artist = "spotify_artist", _("Spotify Artist")  # type:ignore[reportCallIssue]
    # TMDB_Person = "tmdb_person", _("TMDB Person")  # type:ignore[reportCallIssue]
    IGDB = "igdb", _("IGDB Game")  # type:ignore[reportCallIssue]
    BGG = "bgg", _("BGG Boardgame")  # type:ignore[reportCallIssue]
    Steam = "steam", _("Steam Game")  # type:ignore[reportCallIssue]
    Bangumi = "bangumi", _("Bangumi")  # type:ignore[reportCallIssue]
    ApplePodcast = "apple_podcast", _("Apple Podcast")  # type:ignore[reportCallIssue]
    AppleMusic = "apple_music", _("Apple Music")  # type:ignore[reportCallIssue]
    Fediverse = "fedi", _("Fediverse")  # type:ignore[reportCallIssue]
    Qidian = "qidian", _("Qidian")  # type:ignore[reportCallIssue]
    Ypshuo = "ypshuo", _("Ypshuo")  # type:ignore[reportCallIssue]
    AO3 = "ao3", _("Archive of Our Own")  # type:ignore[reportCallIssue]
    JJWXC = "jjwxc", _("JinJiang")  # type:ignore[reportCallIssue]


IdealIdTypes = [
    IdType.ISBN,
    IdType.CUBN,
    IdType.ASIN,
    IdType.GTIN,
    IdType.ISRC,
    IdType.MusicBrainz,
    IdType.RSS,
    IdType.IMDB,
]


class ItemType(models.TextChoices):
    Book = "book", _("Book")  # type:ignore[reportCallIssue]
    TVShow = "tvshow", _("TV Serie")  # type:ignore[reportCallIssue]
    TVSeason = "tvseason", _("TV Season")  # type:ignore[reportCallIssue]
    TVEpisode = "tvepisode", _("TV Episode")  # type:ignore[reportCallIssue]
    Movie = "movie", _("Movie")  # type:ignore[reportCallIssue]
    Album = "music", _("Album")  # type:ignore[reportCallIssue]
    Game = "game", _("Game")  # type:ignore[reportCallIssue]
    Podcast = "podcast", _("Podcast Program")  # type:ignore[reportCallIssue]
    PodcastEpisode = "podcastepisode", _("Podcast Episode")  # type:ignore[reportCallIssue]
    Performance = "performance", _("Performance")  # type:ignore[reportCallIssue]
    PerformanceProduction = "production", _("Production")  # type:ignore[reportCallIssue]
    FanFic = "fanfic", _("Fanfic")  # type:ignore[reportCallIssue]
    Exhibition = "exhibition", _("Exhibition")  # type:ignore[reportCallIssue]
    Collection = "collection", _("Collection")  # type:ignore[reportCallIssue]


class ItemCategory(models.TextChoices):
    Book = "book", _("Book")  # type:ignore[reportCallIssue]
    Movie = "movie", _("Movie")  # type:ignore[reportCallIssue]
    TV = "tv", _("TV")  # type:ignore[reportCallIssue]
    Music = "music", _("Music")  # type:ignore[reportCallIssue]
    Game = "game", _("Game")  # type:ignore[reportCallIssue]
    Podcast = "podcast", _("Podcast")  # type:ignore[reportCallIssue]
    Performance = "performance", _("Performance")  # type:ignore[reportCallIssue]
    FanFic = "fanfic", _("FanFic")  # type:ignore[reportCallIssue]
    Exhibition = "exhibition", _("Exhibition")  # type:ignore[reportCallIssue]
    Collection = "collection", _("Collection")  # type:ignore[reportCallIssue]


class AvailableItemCategory(models.TextChoices):
    Book = "book", _("Book")  # type:ignore[reportCallIssue]
    Movie = "movie", _("Movie")  # type:ignore[reportCallIssue]
    TV = "tv", _("TV")  # type:ignore[reportCallIssue]
    Music = "music", _("Music")  # type:ignore[reportCallIssue]
    Game = "game", _("Game")  # type:ignore[reportCallIssue]
    Podcast = "podcast", _("Podcast")  # type:ignore[reportCallIssue]
    Performance = "performance", _("Performance")  # type:ignore[reportCallIssue]


# class SubItemType(models.TextChoices):
#     Season = "season", _("season")  # type:ignore[reportCallIssue]
#     Episode = "episode", _("episode")  # type:ignore[reportCallIssue]
#     Version = "production", _("production")  # type:ignore[reportCallIssue]


# class CreditType(models.TextChoices):
#     Author = 'author', _('author')
#     Translater = 'translater', _('translater')
#     Producer = 'producer', _('producer')
#     Director = 'director', _('director')
#     Actor = 'actor', _('actor')
#     Playwright = 'playwright', _('playwright')
#     VoiceActor = 'voiceactor', _('voiceactor')
#     Host = 'host', _('host')
#     Developer = 'developer', _('developer')
#     Publisher = 'publisher', _('publisher')


class PrimaryLookupIdDescriptor(object):  # TODO make it mixin of Field
    def __init__(self, id_type: IdType):
        self.id_type = id_type

    def __get__(
        self, instance: "Item | None", cls: type[Any] | None = None
    ) -> str | Self | None:
        if instance is None:
            return self
        if self.id_type != instance.primary_lookup_id_type:
            return None
        return instance.primary_lookup_id_value

    def __set__(self, instance: "Item", id_value: str | None):
        if id_value:
            instance.primary_lookup_id_type = self.id_type
            instance.primary_lookup_id_value = id_value
        else:
            instance.primary_lookup_id_type = None
            instance.primary_lookup_id_value = None


class LookupIdDescriptor(object):  # TODO make it mixin of Field
    def __init__(self, id_type: IdType):
        self.id_type = id_type

    def __get__(self, instance, cls=None):
        if instance is None:
            return self
        return instance.get_lookup_id(self.id_type)

    def __set__(self, instance, value):
        instance.set_lookup_id(self.id_type, value)


# class ItemId(models.Model):
#     item = models.ForeignKey('Item', models.CASCADE)
#     id_type = models.CharField(_("Id Type"), blank=False, choices=IdType.choices, max_length=50)
#     id_value = models.CharField(_("ID Value"), blank=False, max_length=1000)


# class ItemCredit(models.Model):
#     item = models.ForeignKey('Item', models.CASCADE)
#     credit_type = models.CharField(_("Credit Type"), choices=CreditType.choices, blank=False, max_length=50)
#     name = models.CharField(_("Name"), blank=False, max_length=1000)


# def check_source_id(sid):
#     if not sid:
#         return True
#     s = sid.split(':')
#     if len(s) < 2:
#         return False
#     return sid[0] in IdType.values()


class ExternalResourceSchema(Schema):
    url: str


class BaseSchema(Schema):
    id: str = Field(alias="absolute_url")
    type: str = Field(alias="ap_object_type")
    uuid: str
    url: str
    api_url: str
    category: ItemCategory
    parent_uuid: str | None
    display_title: str
    external_resources: list[ExternalResourceSchema] | None


class LocalizedTitleSchema(Schema):
    lang: str
    text: str


class ItemInSchema(Schema):
    title: str = Field(alias="display_title")
    description: str = Field(default="", alias="display_description")
    localized_title: list[LocalizedTitleSchema] = []
    localized_description: list[LocalizedTitleSchema] = []
    cover_image_url: str | None
    rating: float | None
    rating_count: int | None
    # brief is deprecated
    brief: str = Field(deprecated=True, alias="display_description")


class ItemSchema(BaseSchema, ItemInSchema):
    pass


def get_locale_choices_for_jsonform(choices):
    """return list for jsonform schema"""
    return [{"title": v, "value": k} for k, v in choices]


LOCALE_CHOICES_JSONFORM = get_locale_choices_for_jsonform(LOCALE_CHOICES)
LANGUAGE_CHOICES_JSONFORM = get_locale_choices_for_jsonform(LANGUAGE_CHOICES)

LOCALIZED_TITLE_SCHEMA = {
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

LIST_OF_ONE_PLUS_STR_SCHEMA = {
    "type": "list",
    "items": {"type": "string", "required": True},
    "minItems": 1,
    "uniqueItems": True,
}


def LanguageListField():
    return jsondata.ArrayField(
        verbose_name=_("language"),
        base_field=models.CharField(blank=True, default="", max_length=100),
        null=True,
        blank=True,
        default=list,
        # schema={
        #     "type": "list",
        #     "items": {"type": "string", "choices": LANGUAGE_CHOICES_JSONFORM},
        # },
    )


class Item(PolymorphicModel):
    if TYPE_CHECKING:
        external_resources: QuerySet["ExternalResource"]
        collections: QuerySet["Collection"]
        merged_from_items: QuerySet["Item"]
        merged_to_item_id: int
    category: ItemCategory  # subclass must specify this
    url_path = "item"  # subclass must specify this
    child_class = None  # subclass may specify this to allow link to parent item
    parent_class = None  # subclass may specify this to allow create child item
    uid = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    title = models.CharField(_("title"), max_length=1000, default="")
    brief = models.TextField(_("description"), blank=True, default="")
    primary_lookup_id_type = models.CharField(
        _("Primary ID Type"), blank=False, null=True, max_length=50
    )
    primary_lookup_id_value = models.CharField(
        _("Primary ID Value"),
        blank=False,
        null=True,
        max_length=1000,
        help_text="automatically detected, usually no change necessary, left empty if unsure",
    )
    metadata = models.JSONField(_("metadata"), blank=True, null=True, default=dict)
    cover = models.ImageField(
        _("cover"),
        upload_to=item_cover_path,
        default=settings.DEFAULT_ITEM_COVER,
        blank=True,
    )
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    merged_to_item = models.ForeignKey(
        "Item",
        null=True,
        on_delete=models.SET_NULL,
        default=None,
        related_name="merged_from_items",
    )

    localized_title = jsondata.JSONField(
        verbose_name=_("title"),
        null=False,
        blank=True,
        default=list,
        schema=LOCALIZED_TITLE_SCHEMA,
    )

    localized_description = jsondata.JSONField(
        verbose_name=_("description"),
        null=False,
        blank=True,
        default=list,
        schema=LOCALIZED_DESCRIPTION_SCHEMA,
    )

    class Meta:
        index_together = [
            [
                "primary_lookup_id_type",
                "primary_lookup_id_value",
            ]
        ]

    def can_soft_delete(self):
        return (
            not self.is_deleted
            and not self.merged_to_item_id
            and not self.merged_from_items.exists()
            and not self.child_items.exists()
        )

    def delete(
        self,
        using: Any = None,
        keep_parents: bool = False,
        soft: bool = True,
        *args: tuple[Any, ...],
        **kwargs: dict[str, Any],
    ) -> tuple[int, dict[str, int]]:
        if soft:
            self.clear()
            self.is_deleted = True
            self.save(using=using)
            return 0, {}
        else:
            return super().delete(
                using=using, keep_parents=keep_parents, *args, **kwargs
            )

    @cached_property
    def history(self):
        # can't use AuditlogHistoryField bc it will only return history with current content type
        return LogEntry.objects.filter(
            object_id=self.pk, content_type_id__in=list(item_content_types().values())
        )

    @cached_property
    def last_editor(self) -> "User | None":
        last_edit = self.history.order_by("-timestamp").first()
        return last_edit.actor if last_edit else None

    def clear(self):
        self.set_parent_item(None)
        self.primary_lookup_id_value = None
        self.primary_lookup_id_type = None
        for res in self.external_resources.all():
            res.item = None
            res.save()

    def __str__(self):
        return f"{self.__class__.__name__}|{self.pk}|{self.uuid} {self.primary_lookup_id_type}:{self.primary_lookup_id_value if self.primary_lookup_id_value else ''} ({self.display_title})"

    @classmethod
    def lookup_id_type_choices(cls):
        return IdType.choices

    @classmethod
    def lookup_id_cleanup(
        cls, lookup_id_type: str | IdType, lookup_id_value: str
    ) -> tuple[str | IdType, str] | tuple[None, None]:
        if not lookup_id_type or not lookup_id_value or not lookup_id_value.strip():
            return None, None
        return lookup_id_type, lookup_id_value.strip()

    @classmethod
    def get_best_lookup_id(cls, lookup_ids: dict[str, str]) -> tuple[str, str]:
        """get best available lookup id, ideally commonly used"""
        for t in IdealIdTypes:
            if lookup_ids.get(t):
                return t, lookup_ids[t]
        return list(lookup_ids.items())[0]

    @property
    def parent_item(self):
        return None

    @property
    def child_items(self) -> "QuerySet[Item]":
        return Item.objects.none()

    @property
    def child_item_ids(self) -> list[int]:
        return list(self.child_items.values_list("id", flat=True))

    def set_parent_item(self, value: "Item | None"):
        # raise ValueError("cannot set parent item")
        pass

    @property
    def parent_uuid(self) -> str | None:
        return self.parent_item.uuid if self.parent_item else None

    @property
    def sibling_items(self) -> "QuerySet[Item]":
        return Item.objects.none()

    @property
    def title_deco(self) -> str:
        return ""

    @property
    def sibling_item_ids(self) -> list[int]:
        return list(self.sibling_items.values_list("id", flat=True))

    @classmethod
    def get_ap_object_type(cls) -> str:
        return cls.__name__

    @property
    def ap_object_type(self) -> str:
        return self.get_ap_object_type()

    @property
    def ap_object_ref(self) -> dict[str, Any]:
        o = {
            "type": self.get_ap_object_type(),
            "href": self.absolute_url,
            "name": self.title,
        }
        if self.has_cover():
            o["image"] = self.cover_image_url
        return o

    def log_action(self, changes: dict[str, Any]):
        LogEntry.objects.log_create(  # type: ignore
            self, action=LogEntry.Action.UPDATE, changes=changes
        )

    def merge_to(self, to_item: "Item | None"):
        if to_item is None:
            if self.merged_to_item is not None:
                self.merged_to_item = None
                self.save()
            return
        if to_item.pk == self.pk:
            raise ValueError("cannot merge to self")
        if to_item.merged_to_item is not None:
            raise ValueError("cannot merge to item which is merged to another item")
        if not isinstance(to_item, self.__class__):
            raise ValueError("cannot merge to item in a different model")
        self.log_action({"!merged": [str(self.merged_to_item), str(to_item)]})
        self.merged_to_item = to_item
        self.save()
        for res in self.external_resources.all():
            res.item = to_item
            res.save()

    @property
    def final_item(self) -> Self:
        if self.merged_to_item:
            return self.merged_to_item.final_item
        return self

    def recast_to(self, model: "type[Any]") -> "Item":
        logger.warning(f"recast item {self} to {model}")
        if isinstance(self, model):
            return self
        if not issubclass(model, Item):
            raise ValueError("invalid model to recast to")
        ct = ContentType.objects.get_for_model(model)
        old_ct = self.polymorphic_ctype
        if not old_ct:
            raise ValueError("cannot recast item without polymorphic_ctype")
        tbl = self.__class__._meta.db_table
        with disable_auditlog():
            # disable audit as serialization won't work here
            obj = model(item_ptr_id=self.pk, polymorphic_ctype=ct)
            obj.save_base(raw=True)
            obj.save(update_fields=["polymorphic_ctype"])
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {tbl} WHERE item_ptr_id = %s", [self.pk])
        obj = model.objects.get(pk=obj.pk)
        obj.log_action({"!recast": [old_ct.model, ct.model]})
        return obj

    @property
    def uuid(self):
        return b62_encode(self.uid.int).zfill(22)

    @property
    def url(self):
        return f"/{self.url_path}/{self.uuid}"

    @property
    def absolute_url(self):
        return f"{settings.SITE_INFO['site_url']}{self.url}"

    @property
    def api_url(self):
        return f"/api{self.url}"

    @property
    def class_name(self) -> str:
        return self.__class__.__name__.lower()

    def get_localized_title(self) -> str | None:
        if self.localized_title:
            locales = get_current_locales()
            for loc in locales:
                v = next(
                    filter(lambda t: t["lang"] == loc, self.localized_title), {}
                ).get("text")
                if v:
                    return v

    def get_localized_description(self) -> str | None:
        if self.localized_description:
            locales = get_current_locales()
            for loc in locales:
                v = next(
                    filter(lambda t: t["lang"] == loc, self.localized_description), {}
                ).get("text")
                if v:
                    return v

    @cached_property
    def display_title(self) -> str:
        # return title in current locale if possible, otherwise any title
        return (self.get_localized_title() or self.title) or (
            self.localized_title[0]["text"] if self.localized_title else ""
        )

    @cached_property
    def additional_title(self) -> list[str]:
        title = self.display_title
        return [t["text"] for t in self.localized_title if t["text"] != title]

    @cached_property
    def display_description(self) -> str:
        return (
            self.get_localized_description()
            or self.brief
            or (
                self.localized_description[0]["text"]
                if self.localized_description
                else ""
            )
        )

    @property
    def brief_description(self):
        return (str(self.display_description) or "")[:155]

    def to_indexable_titles(self) -> list[str]:
        titles = [t["text"] for t in self.localized_title if t["text"]]
        if self.parent_item:
            titles += self.parent_item.to_indexable_titles()
        return list(set(titles))

    @classmethod
    def get_by_url(cls, url_or_b62: str, resolve_merge=False) -> "Self | None":
        b62 = url_or_b62.strip().split("/")[-1]
        if len(b62) not in [21, 22]:
            r = re.search(r"[A-Za-z0-9]{21,22}", url_or_b62)
            if r:
                b62 = r[0]
        try:
            item = cls.objects.get(uid=uuid.UUID(int=b62_decode(b62)))
            if resolve_merge:
                resolve_cnt = 5
                while item.merged_to_item and resolve_cnt > 0:
                    item = item.merged_to_item
                    resolve_cnt -= 1
                if resolve_cnt == 0:
                    logger.error(f"resolve merge loop for {item}")
                    item = None
        except Exception:
            item = None
        return item

    @classmethod
    def get_by_remote_url(cls, url: str) -> "Self | None":
        url_ = url.replace("/~neodb~/", "/")
        if url_.startswith(settings.SITE_INFO["site_url"]):
            return cls.get_by_url(url_, True)
        er = ExternalResource.objects.filter(url=url_).first()
        return er.item if er else None

    # def get_lookup_id(self, id_type: str) -> str:
    #     prefix = id_type.strip().lower() + ':'
    #     return next((x[len(prefix):] for x in self.lookup_ids if x.startswith(prefix)), None)

    def update_lookup_ids(self, lookup_ids: list[tuple[str, str]]):
        for t, v in lookup_ids:
            if t in IdealIdTypes and self.primary_lookup_id_type not in IdealIdTypes:
                self.primary_lookup_id_type = t
                self.primary_lookup_id_value = v
                return
            if t == self.primary_lookup_id_type:
                self.primary_lookup_id_value = v

    METADATA_COPY_LIST = [
        # "title",
        # "brief",
        "localized_title",
        "localized_description",
    ]  # list of metadata keys to copy from resource to item
    METADATA_MERGE_LIST = [
        "localized_title",
        "localized_description",
    ]

    @classmethod
    def copy_metadata(cls, metadata: dict[str, Any]) -> dict[str, Any]:
        return dict(
            (k, v)
            for k, v in metadata.items()
            if k in cls.METADATA_COPY_LIST and v is not None
        )

    def has_cover(self) -> bool:
        return bool(self.cover) and self.cover != settings.DEFAULT_ITEM_COVER

    @property
    def cover_image_url(self) -> str | None:
        return (
            f"{settings.SITE_INFO['site_url']}{self.cover.url}"  # type:ignore
            if self.cover and self.cover != settings.DEFAULT_ITEM_COVER
            else None
        )

    @property
    def default_cover_image_url(self) -> str:
        return f"{settings.SITE_INFO['site_url']}{settings.DEFAULT_ITEM_COVER}"

    def merge_data_from_external_resource(
        self, p: "ExternalResource", ignore_existing_content: bool = False
    ):
        for k in self.METADATA_COPY_LIST:
            v = p.metadata.get(k)
            if v:
                if not getattr(self, k) or ignore_existing_content:
                    setattr(self, k, v)
                elif k in self.METADATA_MERGE_LIST:
                    setattr(self, k, uniq(getattr(self, k, []) + (v or [])))
        if p.cover and (not self.has_cover() or ignore_existing_content):
            self.cover = p.cover

    def merge_data_from_external_resources(self, ignore_existing_content: bool = False):
        """Subclass may override this"""
        lookup_ids = []
        for p in self.external_resources.all():
            lookup_ids.append((p.id_type, p.id_value))
            lookup_ids += p.other_lookup_ids.items()
            self.merge_data_from_external_resource(p, ignore_existing_content)
        self.update_lookup_ids(list(set(lookup_ids)))

    def update_linked_items_from_external_resource(self, resource: "ExternalResource"):
        """Subclass should override this"""
        pass

    def skip_index(self):
        return False

    @property
    def editable(self):
        return not self.is_deleted and self.merged_to_item is None

    @property
    def rating(self):
        from journal.models import Rating

        return Rating.get_rating_for_item(self)

    @property
    def rating_count(self):
        from journal.models import Rating

        return Rating.get_rating_count_for_item(self)

    @property
    def rating_dist(self):
        from journal.models import Rating

        return Rating.get_rating_distribution_for_item(self)

    @property
    def tags(self):
        from journal.models import TagManager

        return TagManager.indexable_tags_for_item(self)

    def journal_exists(self):
        from journal.models import journal_exists_for_item

        return journal_exists_for_item(self)


class ItemLookupId(models.Model):
    item = models.ForeignKey(
        Item, null=True, on_delete=models.SET_NULL, related_name="lookup_ids"
    )
    id_type = models.CharField(
        _("source site"), blank=True, choices=IdType.choices, max_length=50
    )
    id_value = models.CharField(_("ID on source site"), blank=True, max_length=1000)
    raw_url = models.CharField(
        _("source url"), blank=True, max_length=1000, unique=True
    )

    class Meta:
        unique_together = [["id_type", "id_value"]]


class ExternalResource(models.Model):
    if TYPE_CHECKING:
        required_resources: list[dict[str, str]]
        related_resources: list[dict[str, str]]
        prematched_resources: list[dict[str, str]]
    item = models.ForeignKey(
        Item, null=True, on_delete=models.SET_NULL, related_name="external_resources"
    )
    id_type = models.CharField(
        _("IdType of the source site"),
        blank=False,
        choices=IdType.choices,
        max_length=50,
    )
    id_value = models.CharField(
        _("Primary Id on the source site"), blank=False, max_length=1000
    )
    url = models.CharField(
        _("url to the resource"), blank=False, max_length=1000, unique=True
    )
    cover = models.ImageField(
        upload_to=resource_cover_path, default=settings.DEFAULT_ITEM_COVER, blank=True
    )
    other_lookup_ids = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)
    scraped_time = models.DateTimeField(null=True)
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    required_resources = jsondata.ArrayField(
        models.CharField(), null=False, blank=False, default=list
    )  # type: ignore
    """ links required to generate Item from this resource, e.g. parent TVShow of TVSeason """

    related_resources = jsondata.ArrayField(
        models.CharField(), null=False, blank=False, default=list
    )  # type: ignore
    """links related to this resource which may be fetched later, e.g. sub TVSeason of TVShow"""

    prematched_resources = jsondata.ArrayField(
        models.CharField(), null=False, blank=False, default=list
    )  # type: ignore
    """links to help match an existing Item from this resource"""

    class Meta:
        unique_together = [["id_type", "id_value"]]

    def __str__(self):
        return f"{self.pk}:{self.id_type}:{self.id_value or ''} ({self.url})"

    def unlink_from_item(self):
        if not self.item:
            return
        self.item.log_action({"!unlink": [str(self), None]})
        self.item = None
        self.save()

    def get_site(self):
        from .sites import SiteManager

        return SiteManager.get_site_cls_by_id_type(self.id_type)

    @property
    def site_name(self) -> SiteName:
        try:
            site = self.get_site()
            return site.SITE_NAME if site else SiteName.Unknown
        except Exception:
            logger.warning(f"Unknown site for {self}")
            return SiteName.Unknown

    @property
    def site_label(self) -> str:
        if self.id_type == IdType.Fediverse:
            from takahe.utils import Takahe

            domain = self.id_value.split("://")[1].split("/")[0]
            n = Takahe.get_node_name_for_domain(domain)
            return n or domain
        return self.site_name.label

    def update_content(self, resource_content: "ResourceContent"):
        self.other_lookup_ids = resource_content.lookup_ids
        self.metadata = resource_content.metadata
        if (
            resource_content.metadata.get("cover_image_url")
            and not resource_content.cover_image
        ):
            from .downloaders import BasicImageDownloader

            (
                resource_content.cover_image,
                resource_content.cover_image_extention,
            ) = BasicImageDownloader.download_image(
                resource_content.metadata.get("cover_image_url"), self.url
            )
        if resource_content.cover_image and resource_content.cover_image_extention:
            self.cover = SimpleUploadedFile(
                "temp." + resource_content.cover_image_extention,
                resource_content.cover_image,
            )
        elif resource_content.metadata.get("cover_image_path"):
            self.cover = resource_content.metadata.get("cover_image_path")
        self.scraped_time = timezone.now()
        self.save()

    @property
    def ready(self):
        return bool(self.metadata and self.scraped_time)

    def get_all_lookup_ids(self) -> dict[str, str]:
        d = self.other_lookup_ids.copy()
        d[self.id_type] = self.id_value
        d = {k: v for k, v in d.items() if bool(v)}
        return d

    def get_lookup_ids(
        self, default_model: type[Item] | None = None
    ) -> list[tuple[str, str]]:
        lookup_ids = self.get_all_lookup_ids()
        model = self.get_item_model(default_model)
        bt, bv = model.get_best_lookup_id(lookup_ids)
        ids = [(t, v) for t, v in lookup_ids.items() if t and v and t != bt]
        if bt and bv:
            ids = [(bt, bv)] + ids
        return ids

    def get_item_model(self, default_model: type[Item] | None) -> type[Item]:
        model = self.metadata.get("preferred_model")
        if model:
            m = ContentType.objects.filter(
                app_label="catalog", model=model.lower()
            ).first()
            if m:
                mc: type[Item] | None = m.model_class()  # type: ignore
                if not mc:
                    raise ValueError(
                        f"preferred model {model} does not exist in ContentType"
                    )
                return mc
            else:
                raise ValueError(f"preferred model {model} does not exist")
        if not default_model:
            raise ValueError("no default preferred model specified")
        return default_model


_CONTENT_TYPE_LIST = None


def item_content_types() -> dict[type[Item], int]:
    global _CONTENT_TYPE_LIST
    if _CONTENT_TYPE_LIST is None:
        _CONTENT_TYPE_LIST = {}
        for cls in Item.__subclasses__():
            _CONTENT_TYPE_LIST[cls] = ContentType.objects.get(
                app_label="catalog", model=cls.__name__.lower()
            ).id
    return _CONTENT_TYPE_LIST


_CATEGORY_LIST = None


def item_categories() -> dict[ItemCategory, list[type[Item]]]:
    global _CATEGORY_LIST
    if _CATEGORY_LIST is None:
        _CATEGORY_LIST = {}
        for cls in Item.__subclasses__():
            c = getattr(cls, "category", None)
            if c not in _CATEGORY_LIST:
                _CATEGORY_LIST[c] = [cls]
            else:
                _CATEGORY_LIST[c].append(cls)
    return _CATEGORY_LIST
