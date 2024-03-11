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
        verbose_name=_("other title"),
        null=True,
        blank=True,
        default=list,
    )

    designer = jsondata.ArrayField(
        base_field=models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("designer"),
        null=True,
        blank=True,
        default=list,
    )

    artist = jsondata.ArrayField(
        base_field=models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("artist"),
        null=True,
        blank=True,
        default=list,
    )

    developer = jsondata.ArrayField(
        base_field=models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("developer"),
        null=True,
        blank=True,
        default=list,
    )

    publisher = jsondata.ArrayField(
        base_field=models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("publisher"),
        null=True,
        blank=True,
        default=list,
    )

    release_year = jsondata.IntegerField(
        verbose_name=_("year of publication"), null=True, blank=True
    )

    release_date = jsondata.DateField(
        verbose_name=_("date of publication"),
        auto_now=False,
        auto_now_add=False,
        null=True,
        blank=True,
        help_text=_("YYYY-MM-DD"),
    )

    genre = jsondata.ArrayField(
        verbose_name=_("genre"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
    )

    platform = jsondata.ArrayField(
        verbose_name=_("platform"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        default=list,
    )

    official_site = jsondata.CharField(
        verbose_name=_("website"), max_length=1000, null=True, blank=True
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
