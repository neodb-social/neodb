from datetime import date

from django.db import models
from django.utils.translation import gettext_lazy as _

from .common import (
    LIST_OF_STR_SCHEMA,
    jsondata,
)
from .item import (
    BaseSchema,
    IdType,
    Item,
    ItemCategory,
    ItemInSchema,
    ItemType,
    PrimaryLookupIdDescriptor,
)


class GameReleaseType(models.TextChoices):
    # Unspecified = "", _("Unspecified")
    GAME = "game", _("Main Game")
    EXPANSION = "expansion", _("Expansion")
    DLC = "dlc", _("Downloadable Content")
    MOD = "mod", _("Mod")
    BUNDLE = "bundle", _("Bundle")
    REMASTER = "remaster", _("Remaster")
    REMAKE = "remake", _("Remake")
    SPECIAL = "special", _("Special Edition")
    OTHER = "other", _("Other")


class GameInSchema(ItemInSchema):
    genre: list[str]
    developer: list[str]
    publisher: list[str]
    platform: list[str]
    release_type: str | None = None
    release_date: date | None = None
    official_site: str | None = None


class GameSchema(GameInSchema, BaseSchema):
    pass


class Game(Item):
    schema = GameSchema
    category = ItemCategory.Game
    type = ItemType.Game
    url_path = "game"
    igdb = PrimaryLookupIdDescriptor(IdType.IGDB)
    steam = PrimaryLookupIdDescriptor(IdType.Steam)
    douban_game = PrimaryLookupIdDescriptor(IdType.DoubanGame)
    itch = PrimaryLookupIdDescriptor(IdType.Itch)

    METADATA_COPY_LIST = [
        "localized_title",
        "designer",
        "artist",
        "developer",
        "publisher",
        "release_year",
        "release_date",
        "release_type",
        "genre",
        "platform",
        "official_site",
        "localized_description",
    ]

    designer = jsondata.JSONField(
        verbose_name=_("designer"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )

    artist = jsondata.JSONField(
        verbose_name=_("artist"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )

    developer = jsondata.JSONField(
        verbose_name=_("developer"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )

    publisher = jsondata.JSONField(
        verbose_name=_("publisher"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
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

    release_type = jsondata.CharField(
        verbose_name=_("release type"),
        max_length=100,
        blank=True,
        choices=GameReleaseType.choices,
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
            IdType.Itch,
            IdType.BGG,
            IdType.DoubanGame,
            IdType.Bangumi,
        ]
        return [(i.value, i.label) for i in id_types]

    def to_indexable_doc(self):
        d = super().to_indexable_doc()
        d["people"] = (self.designer or []) + (self.artist or [])
        d["company"] = (self.developer or []) + (self.publisher or [])
        d["date"] = (
            [int(self.release_date.strftime("%Y%m%d"))] if self.release_date else []
        )
        d["genre"] = self.genre or []  # type:ignore
        d["format"] = [self.release_type] if self.release_type else []
        d["format"] += list(self.platform or [])  # type:ignore
        return d

    def to_schema_org(self):
        data = super().to_schema_org()
        data["@type"] = "VideoGame"

        if self.genre:
            data["genre"] = self.genre

        if self.platform:
            data["gamePlatform"] = self.platform

        if self.developer:
            data["author"] = [
                {"@type": "Organization", "name": developer}
                for developer in self.developer
            ]

        if self.publisher:
            data["publisher"] = [
                {"@type": "Organization", "name": publisher}
                for publisher in self.publisher
            ]

        if self.release_date:
            data["datePublished"] = self.release_date.isoformat()
        elif self.release_year:
            data["datePublished"] = str(self.release_year)

        if self.official_site:
            data["sameAs"] = self.official_site

        return data
