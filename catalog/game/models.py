from datetime import date

from django.db import models
from django.utils.translation import gettext_lazy as _

from catalog.common import (
    BaseSchema,
    ExternalResource,
    IdType,
    Item,
    ItemCategory,
    ItemInSchema,
    ItemSchema,
    ItemType,
    PrimaryLookupIdDescriptor,
    jsondata,
)


class GameInSchema(ItemInSchema):
    genre: list[str]
    developer: list[str]
    publisher: list[str]
    platform: list[str]
    release_date: date | None = None
    official_site: str | None = None


class GameSchema(GameInSchema, BaseSchema):
    pass


class Game(Item):
    type = ItemType.Game
    category = ItemCategory.Game
    url_path = "game"
    demonstrative = _("这个游戏")
    igdb = PrimaryLookupIdDescriptor(IdType.IGDB)
    steam = PrimaryLookupIdDescriptor(IdType.Steam)
    douban_game = PrimaryLookupIdDescriptor(IdType.DoubanGame)

    METADATA_COPY_LIST = [
        "title",
        "brief",
        "other_title",
        "designer",
        "artist",
        "developer",
        "publisher",
        "release_year",
        "release_date",
        "genre",
        "platform",
        "official_site",
    ]

    other_title = jsondata.ArrayField(
        base_field=models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("其它标题"),
        null=True,
        blank=True,
        default=list,
    )

    designer = jsondata.ArrayField(
        base_field=models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("设计者"),
        null=True,
        blank=True,
        default=list,
    )

    artist = jsondata.ArrayField(
        base_field=models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("艺术家"),
        null=True,
        blank=True,
        default=list,
    )

    developer = jsondata.ArrayField(
        base_field=models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("开发商"),
        null=True,
        blank=True,
        default=list,
    )

    publisher = jsondata.ArrayField(
        base_field=models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("发行商"),
        null=True,
        blank=True,
        default=list,
    )

    release_year = jsondata.IntegerField(verbose_name=_("发布年份"), null=True, blank=True)

    release_date = jsondata.DateField(
        verbose_name=_("发布日期"),
        auto_now=False,
        auto_now_add=False,
        null=True,
        blank=True,
        help_text=_("YYYY-MM-DD"),
    )

    genre = jsondata.ArrayField(
        verbose_name=_("类型"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
    )

    platform = jsondata.ArrayField(
        verbose_name=_("平台"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        default=list,
    )

    official_site = jsondata.CharField(
        verbose_name=_("官方网站"), max_length=1000, null=True, blank=True
    )

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.IGDB,
            IdType.Steam,
            IdType.BGG,
            IdType.DoubanGame,
            IdType.Bangumi,
        ]
        return [(i.value, i.label) for i in id_types]
