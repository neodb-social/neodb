import logging
import re
import uuid
from functools import cached_property
from typing import TYPE_CHECKING, cast

from auditlog.context import disable_auditlog
from auditlog.models import AuditlogHistoryField, LogEntry
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, models
from django.utils import timezone
from django.utils.baseconv import base62
from django.utils.translation import gettext_lazy as _
from ninja import Field, Schema
from polymorphic.models import PolymorphicModel

from catalog.common import jsondata

from .mixins import SoftDeleteMixin
from .utils import DEFAULT_ITEM_COVER, item_cover_path, resource_cover_path

if TYPE_CHECKING:
    from users.models import User

_logger = logging.getLogger(__name__)


class SiteName(models.TextChoices):
    Unknown = "unknown", _("Unknown")
    Douban = "douban", _("Douban")
    Goodreads = "goodreads", _("Goodreads")
    GoogleBooks = "googlebooks", _("Google Books")
    BooksTW = "bookstw", _("BooksTW")
    IMDB = "imdb", _("IMDb")
    TMDB = "tmdb", _("TMDB")
    Bandcamp = "bandcamp", _("Bandcamp")
    Spotify = "spotify", _("Spotify")
    IGDB = "igdb", _("IGDB")
    Steam = "steam", _("Steam")
    Bangumi = "bangumi", _("Bangumi")
    BGG = "bgg", _("BGG")
    # ApplePodcast = "apple_podcast", _("Apple Podcast")
    RSS = "rss", _("RSS")
    Discogs = "discogs", _("Discogs")
    AppleMusic = "apple_music", _("Apple Music")
    Fediverse = "fedi", _("Fediverse")


class IdType(models.TextChoices):
    WikiData = "wikidata", _("WikiData")
    ISBN10 = "isbn10", _("ISBN10")
    ISBN = "isbn", _("ISBN")  # ISBN 13
    ASIN = "asin", _("ASIN")
    ISSN = "issn", _("ISSN")
    CUBN = "cubn", _("CUBN")
    ISRC = "isrc", _("ISRC")  # only for songs
    GTIN = "gtin", _("GTIN UPC EAN")  # GTIN-13, ISBN is separate
    RSS = "rss", _("RSS Feed URL")
    IMDB = "imdb", _("IMDb")
    TMDB_TV = "tmdb_tv", _("TMDB TV Serie")
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
    Bandcamp = "bandcamp", _("Bandcamp")
    Spotify_Album = "spotify_album", _("Spotify Album")
    Spotify_Show = "spotify_show", _("Spotify Podcast")
    Discogs_Release = "discogs_release", ("Discogs Release")
    Discogs_Master = "discogs_master", ("Discogs Master")
    MusicBrainz = "musicbrainz", ("MusicBrainz ID")
    # DoubanBook_Author = "doubanbook_author", _("豆瓣读书作者")
    # DoubanCelebrity = "doubanmovie_celebrity", _("豆瓣电影影人")
    # Goodreads_Author = "goodreads_author", _("Goodreads作者")
    # Spotify_Artist = "spotify_artist", _("Spotify艺术家")
    # TMDB_Person = "tmdb_person", _("TMDB影人")
    IGDB = "igdb", _("IGDB Game")
    BGG = "bgg", _("BGG Boardgame")
    Steam = "steam", _("Steam Game")
    Bangumi = "bangumi", _("Bangumi")
    ApplePodcast = "apple_podcast", _("Apple Podcast")
    AppleMusic = "apple_music", _("Apple Music")
    Fediverse = "fedi", _("Fediverse")


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
    Book = "book", _("Book")
    TVShow = "tvshow", _("TV Serie")
    TVSeason = "tvseason", _("TV Season")
    TVEpisode = "tvepisode", _("TV Episode")
    Movie = "movie", _("Movie")
    Album = "music", _("Album")
    Game = "game", _("Game")
    Podcast = "podcast", _("Podcast Program")
    PodcastEpisode = "podcastepisode", _("Podcast Episode")
    Performance = "performance", _("Performance")
    PerformanceProduction = "production", _("Production")
    FanFic = "fanfic", _("Fanfic")
    Exhibition = "exhibition", _("Exhibition")
    Collection = "collection", _("Collection")


class ItemCategory(models.TextChoices):
    Book = "book", _("Book")
    Movie = "movie", _("Movie")
    TV = "tv", _("TV")
    Music = "music", _("Music")
    Game = "game", _("Game")
    Podcast = "podcast", _("Podcast")
    Performance = "performance", _("Performance")
    FanFic = "fanfic", _("FanFic")
    Exhibition = "exhibition", _("Exhibition")
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
#     Season = "season", _("剧集分季")
#     Episode = "episode", _("剧集分集")
#     Version = "version", _("版本")


# class CreditType(models.TextChoices):
#     Author = 'author', _('作者')
#     Translater = 'translater', _('译者')
#     Producer = 'producer', _('出品人')
#     Director = 'director', _('电影')
#     Actor = 'actor', _('演员')
#     Playwright = 'playwright', _('播客')
#     VoiceActor = 'voiceactor', _('配音')
#     Host = 'host', _('主持人')
#     Developer = 'developer', _('开发者')
#     Publisher = 'publisher', _('出版方')


class PrimaryLookupIdDescriptor(object):  # TODO make it mixin of Field
    def __init__(self, id_type):
        self.id_type = id_type

    def __get__(self, instance, cls=None):
        if instance is None:
            return self
        if self.id_type != instance.primary_lookup_id_type:
            return None
        return instance.primary_lookup_id_value

    def __set__(self, instance, id_value):
        if id_value:
            instance.primary_lookup_id_type = self.id_type
            instance.primary_lookup_id_value = id_value
        else:
            instance.primary_lookup_id_type = None
            instance.primary_lookup_id_value = None


class LookupIdDescriptor(object):  # TODO make it mixin of Field
    def __init__(self, id_type):
        self.id_type = id_type

    def __get__(self, instance, cls=None):
        if instance is None:
            return self
        return instance.get_lookup_id(self.id_type)

    def __set__(self, instance, value):
        instance.set_lookup_id(self.id_type, value)


# class ItemId(models.Model):
#     item = models.ForeignKey('Item', models.CASCADE)
#     id_type = models.CharField(_("源网站"), blank=False, choices=IdType.choices, max_length=50)
#     id_value = models.CharField(_("源网站ID"), blank=False, max_length=1000)


# class ItemCredit(models.Model):
#     item = models.ForeignKey('Item', models.CASCADE)
#     credit_type = models.CharField(_("类型"), choices=CreditType.choices, blank=False, max_length=50)
#     name = models.CharField(_("名字"), blank=False, max_length=1000)


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


class ItemInSchema(Schema):
    title: str
    brief: str
    cover_image_url: str | None
    rating: float | None
    rating_count: int | None


class ItemSchema(ItemInSchema, BaseSchema):
    pass


class Item(SoftDeleteMixin, PolymorphicModel):
    url_path = "item"  # subclass must specify this
    type = None  # subclass must specify this
    child_class = None  # subclass may specify this to allow link to parent item
    parent_class = None  # subclass may specify this to allow create child item
    category: ItemCategory  # subclass must specify this
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
        _("cover"), upload_to=item_cover_path, default=DEFAULT_ITEM_COVER, blank=True
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

    class Meta:
        index_together = [
            [
                "primary_lookup_id_type",
                "primary_lookup_id_value",
            ]
        ]

    @cached_property
    def history(self):
        # can't use AuditlogHistoryField bc it will only return history with current content type
        return LogEntry.objects.filter(
            object_id=self.id, content_type_id__in=list(item_content_types().values())
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
        return f"{self.__class__.__name__}|{self.id}|{self.uuid} {self.primary_lookup_id_type}:{self.primary_lookup_id_value if self.primary_lookup_id_value else ''} ({self.title})"

    @classmethod
    def lookup_id_type_choices(cls):
        return IdType.choices

    @classmethod
    def lookup_id_cleanup(cls, lookup_id_type, lookup_id_value):
        if not lookup_id_type or not lookup_id_value or not lookup_id_value.strip():
            return None, None
        return lookup_id_type, lookup_id_value.strip()

    @classmethod
    def get_best_lookup_id(cls, lookup_ids):
        """get best available lookup id, ideally commonly used"""
        for t in IdealIdTypes:
            if lookup_ids.get(t):
                return t, lookup_ids[t]
        return list(lookup_ids.items())[0]

    @property
    def parent_item(self):
        return None

    @property
    def child_items(self):
        return Item.objects.none()

    @property
    def child_item_ids(self):
        return list(self.child_items.values_list("id", flat=True))

    def set_parent_item(self, value):
        # raise ValueError("cannot set parent item")
        pass

    @property
    def parent_uuid(self):
        return self.parent_item.uuid if self.parent_item else None

    @classmethod
    def get_ap_object_type(cls):
        return cls.__name__

    @property
    def ap_object_type(self):
        return self.get_ap_object_type()

    @property
    def ap_object_ref(self):
        o = {
            "type": self.get_ap_object_type(),
            "href": self.absolute_url,
            "name": self.title,
        }
        if self.has_cover():
            o["image"] = self.cover_image_url
        return o

    def log_action(self, changes):
        LogEntry.objects.log_create(  # type: ignore
            self, action=LogEntry.Action.UPDATE, changes=changes
        )

    def merge_to(self, to_item):
        if to_item is None:
            if self.merged_to_item is not None:
                self.merged_to_item = None
                self.save()
            return
        if to_item.pk == self.pk:
            raise ValueError("cannot merge to self")
        if to_item.merged_to_item is not None:
            raise ValueError("cannot merge to item which is merged to another item")
        if to_item.__class__ != self.__class__:
            raise ValueError("cannot merge to item in a different model")
        self.log_action({"!merged": [str(self.merged_to_item), str(to_item)]})
        self.merged_to_item = to_item
        self.save()
        for res in self.external_resources.all():
            res.item = to_item
            res.save()

    def recast_to(self, model):
        _logger.warn(f"recast item {self} to {model}")
        if self.__class__ == model:
            return self
        if model not in Item.__subclasses__():
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
        return base62.encode(self.uid.int).zfill(22)

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
    def class_name(self):
        return self.__class__.__name__.lower()

    @property
    def display_title(self):
        return self.title

    @classmethod
    def get_by_url(cls, url_or_b62):
        b62 = url_or_b62.strip().split("/")[-1]
        if len(b62) not in [21, 22]:
            r = re.search(r"[A-Za-z0-9]{21,22}", url_or_b62)
            if r:
                b62 = r[0]
        try:
            item = cls.objects.get(uid=uuid.UUID(int=base62.decode(b62)))
        except Exception:
            item = None
        return item

    # def get_lookup_id(self, id_type: str) -> str:
    #     prefix = id_type.strip().lower() + ':'
    #     return next((x[len(prefix):] for x in self.lookup_ids if x.startswith(prefix)), None)

    def update_lookup_ids(self, lookup_ids):
        for t, v in lookup_ids:
            if t in IdealIdTypes and self.primary_lookup_id_type not in IdealIdTypes:
                self.primary_lookup_id_type = t
                self.primary_lookup_id_value = v
                return
            if t == self.primary_lookup_id_type:
                self.primary_lookup_id_value = v

    METADATA_COPY_LIST = [
        "title",
        "brief",
    ]  # list of metadata keys to copy from resource to item

    @classmethod
    def copy_metadata(cls, metadata):
        return dict(
            (k, v)
            for k, v in metadata.items()
            if k in cls.METADATA_COPY_LIST and v is not None
        )

    def has_cover(self):
        return self.cover and self.cover != DEFAULT_ITEM_COVER

    @property
    def cover_image_url(self):
        return (
            f"{settings.SITE_INFO['site_url']}{self.cover.url}"
            if self.cover and self.cover != DEFAULT_ITEM_COVER
            else None
        )

    def merge_data_from_external_resources(self, ignore_existing_content=False):
        """Subclass may override this"""
        lookup_ids = []
        for p in self.external_resources.all():
            lookup_ids.append((p.id_type, p.id_value))
            lookup_ids += p.other_lookup_ids.items()
            for k in self.METADATA_COPY_LIST:
                if p.metadata.get(k) and (
                    not getattr(self, k) or ignore_existing_content
                ):
                    setattr(self, k, p.metadata.get(k))
            if p.cover and (not self.has_cover() or ignore_existing_content):
                self.cover = p.cover
        self.update_lookup_ids(list(set(lookup_ids)))

    def update_linked_items_from_external_resource(self, resource):
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
        upload_to=resource_cover_path, default=DEFAULT_ITEM_COVER, blank=True
    )
    other_lookup_ids = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)
    scraped_time = models.DateTimeField(null=True)
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)
    required_resources = jsondata.ArrayField(
        models.CharField(), null=False, blank=False, default=list
    )  # links required to generate Item from this resource, e.g. parent TVShow of TVSeason
    related_resources = jsondata.ArrayField(
        models.CharField(), null=False, blank=False, default=list
    )  # links related to this resource which may be fetched later, e.g. sub TVSeason of TVShow
    prematched_resources = jsondata.ArrayField(
        models.CharField(), null=False, blank=False, default=list
    )  # links to help match an existing Item from this resource

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
            _logger.warning(f"Unknown site for {self}")
            return SiteName.Unknown

    @property
    def site_label(self):
        if self.id_type == IdType.Fediverse:
            from takahe.utils import Takahe

            domain = self.id_value.split("://")[1].split("/")[0]
            n = Takahe.get_node_name_for_domain(domain)
            return n or domain
        return self.site_name.label

    def update_content(self, resource_content):
        self.other_lookup_ids = resource_content.lookup_ids
        self.metadata = resource_content.metadata
        if resource_content.cover_image and resource_content.cover_image_extention:
            self.cover = SimpleUploadedFile(
                "temp." + resource_content.cover_image_extention,
                resource_content.cover_image,
            )
        else:
            self.cover = resource_content.metadata.get("cover_image_path")
        self.scraped_time = timezone.now()
        self.save()

    @property
    def ready(self):
        return bool(self.metadata and self.scraped_time)

    def get_all_lookup_ids(self):
        d = self.other_lookup_ids.copy()
        d[self.id_type] = self.id_value
        d = {k: v for k, v in d.items() if bool(v)}
        return d

    def get_lookup_ids(self, default_model):
        lookup_ids = self.get_all_lookup_ids()
        model = self.get_item_model(default_model)
        bt, bv = model.get_best_lookup_id(lookup_ids)
        ids = [(t, v) for t, v in lookup_ids.items() if t and v and t != bt]
        if bt and bv:
            ids = [(bt, bv)] + ids
        return ids

    def get_item_model(self, default_model: type[Item]) -> type[Item]:
        model = self.metadata.get("preferred_model")
        if model:
            m = ContentType.objects.filter(
                app_label="catalog", model=model.lower()
            ).first()
            if m:
                return cast(Item, m).model_class()
            else:
                raise ValueError(f"preferred model {model} does not exist")
        return default_model


_CONTENT_TYPE_LIST = None


def item_content_types():
    global _CONTENT_TYPE_LIST
    if _CONTENT_TYPE_LIST is None:
        _CONTENT_TYPE_LIST = {}
        for cls in Item.__subclasses__():
            _CONTENT_TYPE_LIST[cls] = ContentType.objects.get(
                app_label="catalog", model=cls.__name__.lower()
            ).id
    return _CONTENT_TYPE_LIST


_CATEGORY_LIST = None


def item_categories():
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
