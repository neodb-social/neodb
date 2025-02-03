from django.db import models
from django.utils.translation import gettext_lazy as _

from catalog.common import (
    BaseSchema,
    IdType,
    Item,
    ItemCategory,
    ItemInSchema,
    PrimaryLookupIdDescriptor,
    jsondata,
)
from catalog.common.models import LanguageListField


class MovieInSchema(ItemInSchema):
    orig_title: str | None = None
    other_title: list[str]
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
    url_path = "movie"
    imdb = PrimaryLookupIdDescriptor(IdType.IMDB)
    tmdb_movie = PrimaryLookupIdDescriptor(IdType.TMDB_Movie)
    douban_movie = PrimaryLookupIdDescriptor(IdType.DoubanMovie)

    METADATA_COPY_LIST = [
        # "title",
        # "other_title",
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
        # "season_number",
        # "episodes",
        # "single_episode_length",
        "localized_description",
        # "brief",
    ]
    orig_title = jsondata.CharField(
        verbose_name=_("original title"), blank=True, max_length=500
    )
    other_title = jsondata.ArrayField(
        verbose_name=_("other title"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
    )
    director = jsondata.ArrayField(
        verbose_name=_("director"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
    )
    playwright = jsondata.ArrayField(
        verbose_name=_("playwright"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
    )
    actor = jsondata.ArrayField(
        verbose_name=_("actor"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
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
