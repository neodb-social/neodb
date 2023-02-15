from datetime import date
from catalog.common.models import *
from django.utils.translation import gettext_lazy as _
from django.db import models


class AlbumInSchema(ItemInSchema):
    other_title: str | None = None
    genre: list[str]
    artist: list[str]
    company: list[str]
    duration: int | None = None
    release_date: date | None = None
    track_list: str | None = None


class AlbumSchema(AlbumInSchema, BaseSchema):
    barcode: str | None = None
    pass


class Album(Item):
    url_path = "album"
    category = ItemCategory.Music
    demonstrative = _("这张专辑")
    barcode = PrimaryLookupIdDescriptor(IdType.GTIN)
    douban_music = PrimaryLookupIdDescriptor(IdType.DoubanMusic)
    spotify_album = PrimaryLookupIdDescriptor(IdType.Spotify_Album)
    METADATA_COPY_LIST = [
        "title",
        "other_title",
        "artist",
        "company",
        "track_list",
        "brief",
        "album_type",
        "media",
        "disc_count",
        "genre",
        "release_date",
        "duration",
        "bandcamp_album_id",
    ]
    release_date = jsondata.DateField(_("发行日期"), null=True, blank=True)
    duration = jsondata.IntegerField(_("时长"), null=True, blank=True)
    artist = jsondata.ArrayField(
        models.CharField(blank=True, default="", max_length=200),
        verbose_name=_("艺术家"),
        default=list,
    )
    genre = jsondata.CharField(_("流派"), blank=True, default="", max_length=100)
    company = jsondata.ArrayField(
        models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("发行方"),
        null=True,
        blank=True,
        default=list,
    )
    track_list = jsondata.TextField(_("曲目"), blank=True, default="")
    other_title = jsondata.CharField(_("其它标题"), blank=True, default="", max_length=500)
    album_type = jsondata.CharField(_("专辑类型"), blank=True, default="", max_length=500)
    media = jsondata.CharField(_("介质"), blank=True, default="", max_length=500)
    bandcamp_album_id = jsondata.CharField(blank=True, default="", max_length=500)
    disc_count = jsondata.IntegerField(_("碟片数"), blank=True, default="", max_length=500)

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.GTIN,
            IdType.ISRC,
            IdType.Spotify_Album,
            IdType.Bandcamp,
            IdType.DoubanMusic,
        ]
        return [(i.value, i.label) for i in id_types]
