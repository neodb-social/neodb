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
from ninja import Schema
from polymorphic.models import PolymorphicModel

from catalog.common import jsondata
from users.models import User

from .mixins import SoftDeleteMixin
from .utils import DEFAULT_ITEM_COVER, item_cover_path, resource_cover_path

if TYPE_CHECKING:
    from django.utils.functional import _StrOrPromise

_logger = logging.getLogger(__name__)


class SiteName(models.TextChoices):
    Unknown = "unknown", _("未知站点")
    Douban = "douban", _("豆瓣")
    Goodreads = "goodreads", _("Goodreads")
    GoogleBooks = "googlebooks", _("谷歌图书")
    BooksTW = "bookstw", _("博客来")
    IMDB = "imdb", _("IMDb")
    TMDB = "tmdb", _("TMDB")
    Bandcamp = "bandcamp", _("Bandcamp")
    Spotify = "spotify", _("Spotify")
    IGDB = "igdb", _("IGDB")
    Steam = "steam", _("Steam")
    Bangumi = "bangumi", _("Bangumi")
    # ApplePodcast = "apple_podcast", _("苹果播客")
    RSS = "rss", _("RSS")
    Discogs = "discogs", _("Discogs")
    AppleMusic = "apple_music", _("苹果音乐")


class IdType(models.TextChoices):
    WikiData = "wikidata", _("维基数据")
    ISBN10 = "isbn10", _("ISBN10")
    ISBN = "isbn", _("ISBN")  # ISBN 13
    ASIN = "asin", _("ASIN")
    ISSN = "issn", _("ISSN")
    CUBN = "cubn", _("统一书号")
    ISRC = "isrc", _("ISRC")  # only for songs
    GTIN = "gtin", _("GTIN UPC EAN码")  # GTIN-13, ISBN is separate
    RSS = "rss", _("RSS Feed URL")
    IMDB = "imdb", _("IMDb")
    TMDB_TV = "tmdb_tv", _("TMDB剧集")
    TMDB_TVSeason = "tmdb_tvseason", _("TMDB剧集")
    TMDB_TVEpisode = "tmdb_tvepisode", _("TMDB剧集")
    TMDB_Movie = "tmdb_movie", _("TMDB电影")
    Goodreads = "goodreads", _("Goodreads")
    Goodreads_Work = "goodreads_work", _("Goodreads著作")
    GoogleBooks = "googlebooks", _("谷歌图书")
    DoubanBook = "doubanbook", _("豆瓣读书")
    DoubanBook_Work = "doubanbook_work", _("豆瓣读书著作")
    DoubanMovie = "doubanmovie", _("豆瓣电影")
    DoubanMusic = "doubanmusic", _("豆瓣音乐")
    DoubanGame = "doubangame", _("豆瓣游戏")
    DoubanDrama = "doubandrama", _("豆瓣舞台剧")
    DoubanDramaVersion = "doubandrama_version", _("豆瓣舞台剧版本")
    BooksTW = "bookstw", _("博客来图书")
    Bandcamp = "bandcamp", _("Bandcamp")
    Spotify_Album = "spotify_album", _("Spotify专辑")
    Spotify_Show = "spotify_show", _("Spotify播客")
    Discogs_Release = "discogs_release", ("Discogs Release")
    Discogs_Master = "discogs_master", ("Discogs Master")
    MusicBrainz = "musicbrainz", ("MusicBrainz ID")
    DoubanBook_Author = "doubanbook_author", _("豆瓣读书作者")
    DoubanCelebrity = "doubanmovie_celebrity", _("豆瓣电影影人")
    Goodreads_Author = "goodreads_author", _("Goodreads作者")
    Spotify_Artist = "spotify_artist", _("Spotify艺术家")
    TMDB_Person = "tmdb_person", _("TMDB影人")
    IGDB = "igdb", _("IGDB游戏")
    Steam = "steam", _("Steam游戏")
    Bangumi = "bangumi", _("Bangumi")
    ApplePodcast = "apple_podcast", _("苹果播客")
    AppleMusic = "apple_music", _("苹果音乐")


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
    Book = "book", _("书")
    TVShow = "tvshow", _("剧集")
    TVSeason = "tvseason", _("剧集分季")
    TVEpisode = "tvepisode", _("剧集分集")
    Movie = "movie", _("电影")
    Album = "music", _("音乐专辑")
    Game = "game", _("游戏")
    Podcast = "podcast", _("播客")
    PodcastEpisode = "podcastepisode", _("播客单集")
    FanFic = "fanfic", _("网文")
    Performance = "performance", _("剧目")
    PerformanceProduction = "production", _("上演")
    Exhibition = "exhibition", _("展览")
    Collection = "collection", _("收藏单")


class ItemCategory(models.TextChoices):
    Book = "book", _("书")
    Movie = "movie", _("电影")
    TV = "tv", _("剧集")
    Music = "music", _("音乐")
    Game = "game", _("游戏")
    Podcast = "podcast", _("播客")
    FanFic = "fanfic", _("网文")
    Performance = "performance", _("演出")
    Exhibition = "exhibition", _("展览")
    Collection = "collection", _("收藏单")


class AvailableItemCategory(models.TextChoices):
    Book = "book", _("书")
    Movie = "movie", _("电影")
    TV = "tv", _("剧集")
    Music = "music", _("音乐")
    Game = "game", _("游戏")
    Podcast = "podcast", _("播客")
    Performance = "performance", _("演出")
    # FanFic = "fanfic", _("网文")
    # Exhibition = "exhibition", _("展览")
    # Collection = "collection", _("收藏单")


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
    parent_class = None  # subclass may specify this to allow create child item
    category: ItemCategory | None = None  # subclass must specify this
    demonstrative: "_StrOrPromise | None" = None  # subclass must specify this
    uid = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    title = models.CharField(_("标题"), max_length=1000, default="")
    brief = models.TextField(_("简介"), blank=True, default="")
    primary_lookup_id_type = models.CharField(
        _("主要标识类型"), blank=False, null=True, max_length=50
    )
    primary_lookup_id_value = models.CharField(
        _("主要标识数值"), blank=False, null=True, max_length=1000
    )
    metadata = models.JSONField(_("其它信息"), blank=True, null=True, default=dict)
    cover = models.ImageField(
        _("封面"), upload_to=item_cover_path, default=DEFAULT_ITEM_COVER, blank=True
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
    last_editor = models.ForeignKey(
        User, on_delete=models.SET_NULL, related_name="+", null=True, blank=False
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

    def log_action(self, changes):
        LogEntry.objects.log_create(
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
            raise ValueError(f"cannot merge to item in a different model")
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
        except:
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
        _("源网站"), blank=True, choices=IdType.choices, max_length=50
    )
    id_value = models.CharField(_("源网站ID"), blank=True, max_length=1000)
    raw_url = models.CharField(_("源网站ID"), blank=True, max_length=1000, unique=True)

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
    )
    related_resources = jsondata.ArrayField(
        models.CharField(), null=False, blank=False, default=list
    )

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
    def site_name(self):
        try:
            return self.get_site().SITE_NAME
        except:
            _logger.warning(f"Unknown site for {self}")
            return SiteName.Unknown

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

    def get_preferred_model(self) -> type[Item] | None:
        model = self.metadata.get("preferred_model")
        if model:
            m = ContentType.objects.filter(
                app_label="catalog", model=model.lower()
            ).first()
            if m:
                return cast(Item, m).model_class()
            else:
                raise ValueError(f"preferred model {model} does not exist")
        return None


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
