from django.utils.translation import gettext_lazy as _
from ninja import Field

from common.models import (
    duration_to_seconds,
    partial_date_to_int,
    year_of_partial_date,
)

from .common import (
    LIST_OF_STR_SCHEMA,
    CountryListField,
    GenreListField,
    LanguageListField,
    VideoFieldsResolverMixin,
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
from .utils import normalize_legacy_video_metadata


class MovieInSchema(VideoFieldsResolverMixin, ItemInSchema):
    orig_title: str | None = None
    director: list[str]
    playwright: list[str]
    actor: list[str]
    producer: list[str]
    genre: list[str]
    language: list[str]
    origin_country: list[str]
    release_date: str | None = None
    # year is deprecated
    year: int | None = Field(
        None, deprecated="Use the year part of `release_date` instead."
    )
    official_site: str | None = Field(None, alias="site")
    site: str | None = Field(None, deprecated="Use `official_site` instead.")
    length: int | None = None
    duration: str | None = Field(
        None, deprecated="Display string; use `length` (seconds) instead."
    )
    # area and showtime are deprecated
    area: list[str] = Field(
        [], deprecated="Use `origin_country` (ISO 3166-1 alpha-2) instead."
    )
    showtime: list[dict] = Field([], deprecated="Use `release_date` instead.")

    @staticmethod
    def resolve_director(obj: "Movie") -> list[str]:
        return obj.credit_names_by_role("director")

    @staticmethod
    def resolve_playwright(obj: "Movie") -> list[str]:
        return obj.credit_names_by_role("playwright")

    @staticmethod
    def resolve_actor(obj: "Movie") -> list[str]:
        return obj.credit_names_by_role("actor")

    @staticmethod
    def resolve_producer(obj: "Movie") -> list[str]:
        return obj.credit_names_by_role("producer")


class MovieSchema(MovieInSchema, BaseSchema):
    imdb: str | None = None
    pass


class Movie(Item):
    schema = MovieSchema
    category = ItemCategory.Movie
    type = ItemType.Movie
    url_path = "movie"

    available_roles = [
        PeopleRole.DIRECTOR,
        PeopleRole.PLAYWRIGHT,
        PeopleRole.ACTOR,
        PeopleRole.PRODUCER,
        PeopleRole.PRODUCTION_COMPANY,
        PeopleRole.DISTRIBUTOR,
    ]
    imdb = PrimaryLookupIdDescriptor(IdType.IMDB)
    tmdb_movie = PrimaryLookupIdDescriptor(IdType.TMDB_Movie)
    douban_movie = PrimaryLookupIdDescriptor(IdType.DoubanMovie)

    CREDIT_FIELD_MAPPING = {
        "director": "director",
        "playwright": "playwright",
        "actor": "actor",
        "producer": "producer",
    }

    METADATA_COPY_LIST = [
        "localized_title",
        "orig_title",
        "director",
        "playwright",
        "actor",
        "producer",
        "genre",
        "release_date",
        "site",
        "origin_country",
        "language",
        "length",
        "localized_description",
    ]
    orig_title = jsondata.CharField(
        verbose_name=_("original title"), blank=True, max_length=500
    )
    director = jsondata.JSONField(
        verbose_name=_("director"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    playwright = jsondata.JSONField(
        verbose_name=_("playwright"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    actor = jsondata.JSONField(
        verbose_name=_("actor"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    producer = jsondata.JSONField(
        verbose_name=_("producer"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    genre = GenreListField(ItemCategory.Movie)
    release_date = jsondata.CharField(
        verbose_name=_("release date"),
        null=True,
        blank=True,
        max_length=10,
        help_text=_("YYYY, YYYY-MM or YYYY-MM-DD"),
    )
    site = jsondata.URLField(verbose_name=_("website"), blank=True, max_length=200)
    origin_country = CountryListField()
    language = LanguageListField()
    length = jsondata.IntegerField(
        verbose_name=_("length"), null=True, blank=True, help_text=_("seconds")
    )
    season_number = jsondata.IntegerField(
        null=True, blank=True
    )  # TODO remove after migration
    episodes = jsondata.IntegerField(
        null=True, blank=True
    )  # TODO remove after migration
    single_episode_length = jsondata.IntegerField(
        null=True, blank=True
    )  # TODO remove after migration

    @property
    def year(self) -> int | None:
        return year_of_partial_date(self.release_date)

    @classmethod
    def normalize_legacy_metadata(cls, metadata: dict) -> None:
        super().normalize_legacy_metadata(metadata)
        normalize_legacy_video_metadata(metadata)

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.IMDB,
            IdType.TMDB_Movie,
            IdType.DoubanMovie,
            IdType.Bangumi,
        ]
        return [(i.value, i.label) for i in id_types]

    @classmethod
    def lookup_id_cleanup(cls, lookup_id_type, lookup_id_value):
        if lookup_id_type == IdType.IMDB.value and lookup_id_value:
            if lookup_id_value[:2] == "tt":
                return lookup_id_type, lookup_id_value
            else:
                return None, None
        return super().lookup_id_cleanup(lookup_id_type, lookup_id_value)

    def to_indexable_titles(self) -> list[str]:
        titles = [t["text"] for t in self.localized_title if t["text"]]
        titles += [self.orig_title] if self.orig_title else []
        return list(set(titles))

    def to_indexable_doc(self):
        d = super().to_indexable_doc()
        if self.imdb:
            d["lookup_id"] = [str(self.imdb)]
        dt = partial_date_to_int(self.release_date)
        d["date"] = [dt] if dt else []
        d["genre"] = self.genre or []
        return d

    def to_schema_org(self):
        data = super().to_schema_org()
        data["@type"] = "Movie"

        if self.orig_title and self.orig_title != self.display_title:
            data["alternateName"] = self.orig_title

        if self.release_date:
            data["dateCreated"] = self.release_date

        length = duration_to_seconds(self.length)
        if length:
            data["duration"] = f"PT{length // 3600}H{(length % 3600) // 60}M"

        directors = self.credit_names_by_role("director")
        if directors:
            data["director"] = [
                {"@type": "Person", "name": person} for person in directors
            ]

        actors = self.credit_names_by_role("actor")
        if actors:
            data["actor"] = [{"@type": "Person", "name": person} for person in actors]

        if self.genre:
            data["genre"] = self.genre

        return data
