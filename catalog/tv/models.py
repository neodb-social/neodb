"""
Models for TV

TVShow -> TVSeason -> TVEpisode

TVEpisode is not fully implemented at the moment

Three way linking between Douban / IMDB / TMDB are quite messy

IMDB:
most widely used.
no ID for Season, only for Show and Episode

TMDB:
most friendly API.
for some TV specials, both shown as an Episode of Season 0 and a Movie, with same IMDB id

Douban:
most wanted by our users.
for single season show, IMDB id of the show id used
for multi season show, IMDB id for Ep 1 will be used to repensent that season
tv specials are are shown as movies

For now, we follow Douban convention, but keep an eye on it in case it breaks its own rules...

"""
from functools import cached_property
from catalog.common.models import *
from django.db import models
from django.utils.translation import gettext_lazy as _


class TVShowInSchema(ItemInSchema):
    season_count: int | None = None
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
    episode_count: int | None = None


class TVShowSchema(TVShowInSchema, BaseSchema):
    imdb: str | None = None
    # seasons: list['TVSeason']
    pass


class TVSeasonInSchema(ItemInSchema):
    season_number: int | None = None
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
    episode_count: int | None = None


class TVSeasonSchema(TVSeasonInSchema, BaseSchema):
    pass


class TVShow(Item):
    type = ItemType.TVShow
    child_class = "TVSeason"
    category = ItemCategory.TV
    url_path = "tv"
    demonstrative = _("这部剧集")
    imdb = PrimaryLookupIdDescriptor(IdType.IMDB)
    tmdb_tv = PrimaryLookupIdDescriptor(IdType.TMDB_TV)
    imdb = PrimaryLookupIdDescriptor(IdType.IMDB)
    season_count = models.IntegerField(verbose_name=_("总季数"), null=True, blank=True)
    episode_count = models.PositiveIntegerField(
        verbose_name=_("总集数"), null=True, blank=True
    )

    METADATA_COPY_LIST = [
        "title",
        "season_count",
        "orig_title",
        "other_title",
        "director",
        "playwright",
        "actor",
        "brief",
        "genre",
        "showtime",
        "site",
        "area",
        "language",
        "year",
        "duration",
        "episode_count",
        "single_episode_length",
    ]
    orig_title = jsondata.CharField(
        verbose_name=_("原始标题"), blank=True, default="", max_length=500
    )
    other_title = jsondata.ArrayField(
        base_field=models.CharField(blank=True, default="", max_length=500),
        verbose_name=_("其它标题"),
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
        _("播出日期"),
        null=True,
        blank=True,
        default=list,
        schema={
            "type": "array",
            "items": {
                "type": "dict",
                "additionalProperties": False,
                "keys": ["region", "time"],
                "required": ["time"],
            },
        },
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
    single_episode_length = jsondata.IntegerField(
        verbose_name=_("单集长度"), null=True, blank=True
    )
    season_number = jsondata.IntegerField(
        null=True, blank=True
    )  # TODO remove after migration
    duration = jsondata.CharField(
        blank=True, default="", max_length=200
    )  # TODO remove after migration

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.IMDB,
            IdType.TMDB_TV,
            IdType.DoubanMovie,
            IdType.Bangumi,
        ]
        return [(i.value, i.label) for i in id_types]

    @cached_property
    def all_seasons(self):
        return (
            self.seasons.all()
            .order_by("season_number")
            .filter(is_deleted=False, merged_to_item=None)
        )


class TVSeason(Item):
    type = ItemType.TVSeason
    category = ItemCategory.TV
    url_path = "tv/season"
    demonstrative = _("这季剧集")
    douban_movie = PrimaryLookupIdDescriptor(IdType.DoubanMovie)
    imdb = PrimaryLookupIdDescriptor(IdType.IMDB)
    tmdb_tvseason = PrimaryLookupIdDescriptor(IdType.TMDB_TVSeason)
    show = models.ForeignKey(
        TVShow, null=True, on_delete=models.SET_NULL, related_name="seasons"
    )
    season_number = models.PositiveIntegerField(verbose_name=_("本季序号"), null=True)
    episode_count = models.PositiveIntegerField(verbose_name=_("本季集数"), null=True)

    METADATA_COPY_LIST = [
        "title",
        "season_number",
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
        "episode_count",
        "single_episode_length",
        "brief",
    ]
    orig_title = jsondata.CharField(
        verbose_name=_("原始标题"), blank=True, default="", max_length=500
    )
    other_title = jsondata.ArrayField(
        verbose_name=_("其它标题"),
        base_field=models.CharField(blank=True, default="", max_length=500),
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
        _("播出日期"),
        null=True,
        blank=True,
        default=list,
        schema={
            "type": "array",
            "items": {
                "type": "dict",
                "additionalProperties": False,
                "keys": ["region", "time"],
                "required": ["time"],
            },
        },
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
    single_episode_length = jsondata.IntegerField(
        verbose_name=_("单集长度"), null=True, blank=True
    )
    duration = jsondata.CharField(
        blank=True, default="", max_length=200
    )  # TODO remove after migration

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.IMDB,
            IdType.TMDB_TVSeason,
            IdType.DoubanMovie,
        ]
        return [(i.value, i.label) for i in id_types]

    @property
    def display_title(self):
        if self.season_number and not re.match(r"^.+第.+季$", self.title):
            return f"{self.title} 第{self.season_number}季"  # TODO i18n
        else:
            return self.title

    def update_linked_items_from_external_resource(self, resource):
        for w in resource.required_resources:
            if w["model"] == "TVShow":
                p = ExternalResource.objects.filter(
                    id_type=w["id_type"], id_value=w["id_value"]
                ).first()
                if p and p.item:
                    self.show = p.item

    def all_seasons(self):
        return self.show.all_seasons if self.show else []

    @property
    def parent_item(self):
        return self.show

    def set_parent_item(self, value):
        self.show = value


class TVEpisode(Item):
    category = ItemCategory.TV
    url_path = "tv/episode"
    show = models.ForeignKey(
        TVShow, null=True, on_delete=models.SET_NULL, related_name="episodes"
    )
    season = models.ForeignKey(
        TVSeason, null=True, on_delete=models.SET_NULL, related_name="episodes"
    )
    episode_number = models.PositiveIntegerField(null=True)
    imdb = PrimaryLookupIdDescriptor(IdType.IMDB)
    METADATA_COPY_LIST = ["title", "brief", "episode_number"]
