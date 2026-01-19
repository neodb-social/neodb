from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models.misc import int_

from .common import (
    LIST_OF_STR_SCHEMA,
    LanguageListField,
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


class MovieInSchema(ItemInSchema):
    orig_title: str | None = None
    director: list[str]
    playwright: list[str]
    actor: list[str]
    genre: list[str]
    language: list[str]
    area: list[str]
    year: int | None = None
    site: str | None = None
    duration: str | None = None


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

    METADATA_COPY_LIST = [
        "localized_title",
        "orig_title",
        "director",
        "playwright",
        "actor",
        "genre",
        "showtime",
        "site",
        "area",
        "language",
        "year",
        "duration",
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
    genre = jsondata.ArrayField(
        verbose_name=_("genre"),
        base_field=models.CharField(blank=True, default="", max_length=50),
        null=True,
        blank=True,
        default=list,
    )  # , choices=MovieGenreEnum.choices
    showtime = jsondata.JSONField(
        _("release date"),
        null=True,
        blank=True,
        default=list,
        schema={
            "type": "list",
            "items": {
                "type": "dict",
                "additionalProperties": False,
                "keys": {
                    "time": {
                        "type": "string",
                        "title": _("date"),
                        "placeholder": _("required"),
                    },
                    "region": {
                        "type": "string",
                        "title": _("region or event"),
                        "placeholder": _(
                            "Germany or Toronto International Film Festival"
                        ),
                    },
                },
                "required": ["time"],
            },
        },
    )
    site = jsondata.URLField(verbose_name=_("website"), blank=True, max_length=200)
    area = jsondata.ArrayField(
        verbose_name=_("region"),
        base_field=models.CharField(
            blank=True,
            default="",
            max_length=100,
        ),
        null=True,
        blank=True,
        default=list,
    )
    language = LanguageListField()
    year = jsondata.IntegerField(verbose_name=_("year"), null=True, blank=True)
    duration = jsondata.CharField(verbose_name=_("length"), blank=True, max_length=200)
    season_number = jsondata.IntegerField(
        null=True, blank=True
    )  # TODO remove after migration
    episodes = jsondata.IntegerField(
        null=True, blank=True
    )  # TODO remove after migration
    single_episode_length = jsondata.IntegerField(
        null=True, blank=True
    )  # TODO remove after migration

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
        d["people"] = (
            (self.director or []) + (self.actor or []) + (self.playwright or [])
        )
        dt = int_(self.year) * 10000
        d["date"] = [dt] if dt else []
        d["genre"] = self.genre or []  # type:ignore
        return d

    def to_schema_org(self):
        data = super().to_schema_org()
        data["@type"] = "Movie"

        if self.orig_title and self.orig_title != self.display_title:
            data["alternateName"] = self.orig_title

        if self.year:
            data["dateCreated"] = str(self.year)

        if self.duration:
            data["duration"] = self.duration

        if self.director:
            data["director"] = [
                {"@type": "Person", "name": person} for person in self.director
            ]

        if self.actor:
            data["actor"] = [
                {"@type": "Person", "name": person} for person in self.actor
            ]

        if self.genre:
            data["genre"] = self.genre

        return data
