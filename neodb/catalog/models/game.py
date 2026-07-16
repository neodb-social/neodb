from django.db import models
from django.utils.translation import gettext_lazy as _
from ninja import Field

from common.models import partial_date_to_int, year_of_partial_date

from common.models import normalize_game_platforms

from .common import (
    LIST_OF_STR_SCHEMA,
    GamePlatformListField,
    GenreListField,
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
from .people import PeopleRole
from .utils import canonicalize_release_date_key


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
    release_date: str | None = None
    official_site: str | None = None
    # release_year is deprecated
    release_year: int | None = Field(
        None, deprecated="Use the year part of `release_date` instead."
    )

    @staticmethod
    def resolve_release_year(obj: "Game") -> int | None:
        return obj.release_year

    @staticmethod
    def resolve_developer(obj: "Game") -> list[str]:
        return obj.credit_names_by_role("developer")

    @staticmethod
    def resolve_publisher(obj: "Game") -> list[str]:
        return obj.credit_names_by_role("publisher")


class GameSchema(GameInSchema, BaseSchema):
    pass


class Game(Item):
    schema = GameSchema
    category = ItemCategory.Game
    type = ItemType.Game
    url_path = "game"

    available_roles = [
        PeopleRole.DESIGNER,
        PeopleRole.ARTIST,
        PeopleRole.DEVELOPER,
        PeopleRole.PUBLISHER,
        PeopleRole.PRODUCER,
    ]
    CREDIT_FIELD_MAPPING = {
        "designer": "designer",
        "artist": "artist",
        "developer": "developer",
        "publisher": "publisher",
    }

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

    release_date = jsondata.CharField(
        verbose_name=_("date of publication"),
        null=True,
        blank=True,
        max_length=10,
        help_text=_("YYYY, YYYY-MM or YYYY-MM-DD"),
    )

    @property
    def release_year(self) -> int | None:
        return year_of_partial_date(self.release_date)

    @classmethod
    def normalize_legacy_metadata(cls, metadata: dict) -> None:
        super().normalize_legacy_metadata(metadata)
        # Sources: federated peers running older code, ndjson restores,
        # and local rows that predate the unification.
        # - release_year -> release_date when no date is known
        # - free-text release_date (e.g. localized Steam strings) -> partial ISO
        release_year = metadata.pop("release_year", None)
        if release_year and not metadata.get("release_date"):
            metadata["release_date"] = str(release_year)
        if metadata.get("platform"):
            metadata["platform"] = normalize_game_platforms(metadata["platform"])
        canonicalize_release_date_key(metadata)

    release_type = jsondata.CharField(
        verbose_name=_("release type"),
        max_length=100,
        blank=True,
        choices=GameReleaseType.choices,
    )

    genre = GenreListField(ItemCategory.Game)

    platform = GamePlatformListField()

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
            IdType.MobyGames,
            IdType.DoubanGame,
            IdType.Bangumi,
        ]
        return [(i.value, i.label) for i in id_types]

    def to_indexable_doc(self):
        d = super().to_indexable_doc()
        dt = partial_date_to_int(self.release_date)
        d["date"] = [dt] if dt else []
        d["genre"] = self.genre or []
        d["format"] = [self.release_type] if self.release_type else []
        d["format"] += list(self.platform or [])
        return d

    def to_schema_org(self):
        data = super().to_schema_org()
        data["@type"] = "VideoGame"

        if self.genre:
            data["genre"] = self.genre

        if self.platform:
            data["gamePlatform"] = self.platform

        developers = self.credit_names_by_role("developer")
        if developers:
            data["author"] = [
                {"@type": "Organization", "name": developer} for developer in developers
            ]

        publishers = self.credit_names_by_role("publisher")
        if publishers:
            data["publisher"] = [
                {"@type": "Organization", "name": publisher} for publisher in publishers
            ]

        if self.release_date:
            data["datePublished"] = self.release_date

        if self.official_site:
            data["sameAs"] = self.official_site

        return data
