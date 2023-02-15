from catalog.common.models import *
from django.utils.translation import gettext_lazy as _
from django.db import models


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
    duration: int | None = None


class MovieSchema(MovieInSchema, BaseSchema):
    pass


class Movie(Item):
    category = ItemCategory.Movie
    url_path = "movie"
    imdb = PrimaryLookupIdDescriptor(IdType.IMDB)
    tmdb_movie = PrimaryLookupIdDescriptor(IdType.TMDB_Movie)
    douban_movie = PrimaryLookupIdDescriptor(IdType.DoubanMovie)
    demonstrative = _("这部电影")

    METADATA_COPY_LIST = [
        "title",
        "orig_title",
        "other_title",
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
        "brief",
    ]
    orig_title = jsondata.CharField(
        verbose_name=_("原始标题"), blank=True, default="", max_length=500
    )
    other_title = jsondata.ArrayField(
        base_field=models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("其他标题"),
        null=True,
        blank=True,
        default=list,
    )
    director = jsondata.ArrayField(
        verbose_name=_("导演"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
    )
    playwright = jsondata.ArrayField(
        verbose_name=_("编剧"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
    )
    actor = jsondata.ArrayField(
        verbose_name=_("演员"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
    )
    genre = jsondata.ArrayField(
        verbose_name=_("类型"),
        base_field=models.CharField(blank=True, default="", max_length=50),
        null=True,
        blank=True,
        default=list,
    )  # , choices=MovieGenreEnum.choices
    showtime = jsondata.JSONField(
        _("上映日期"),
        null=True,
        blank=True,
        default=list,
    )
    site = jsondata.URLField(
        verbose_name=_("官方网站"), blank=True, default="", max_length=200
    )
    area = jsondata.ArrayField(
        verbose_name=_("国家地区"),
        base_field=models.CharField(
            blank=True,
            default="",
            max_length=100,
        ),
        null=True,
        blank=True,
        default=list,
    )
    language = jsondata.ArrayField(
        verbose_name=_("语言"),
        base_field=models.CharField(
            blank=True,
            default="",
            max_length=100,
        ),
        null=True,
        blank=True,
        default=list,
    )
    year = jsondata.IntegerField(verbose_name=_("年份"), null=True, blank=True)
    duration = jsondata.CharField(
        verbose_name=_("片长"), blank=True, default="", max_length=200
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
