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
from typing import TYPE_CHECKING

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
    PrimaryLookupIdDescriptor,
    jsondata,
)
from catalog.common.models import (
    LANGUAGE_CHOICES_JSONFORM,
    LIST_OF_STR_SCHEMA,
    LanguageListField,
)
from common.models.lang import RE_LOCALIZED_SEASON_NUMBERS, localize_number
from common.models.misc import uniq


class TVShowInSchema(ItemInSchema):
    season_count: int | None = None
    orig_title: str | None = None
    director: list[str]
    playwright: list[str]
    actor: list[str]
    genre: list[str]
    language: list[str]
    area: list[str]
    year: int | None = None
    site: str | None = None
    episode_count: int | None = None
    season_uuids: list[str]


class TVShowSchema(TVShowInSchema, BaseSchema):
    imdb: str | None = None
    # seasons: list['TVSeason']
    pass


class TVSeasonInSchema(ItemInSchema):
    season_number: int | None = None
    orig_title: str | None = None
    director: list[str]
    playwright: list[str]
    actor: list[str]
    genre: list[str]
    language: list[str]
    area: list[str]
    year: int | None = None
    site: str | None = None
    episode_count: int | None = None
    episode_uuids: list[str]


class TVSeasonSchema(TVSeasonInSchema, BaseSchema):
    imdb: str | None = None


class TVEpisodeSchema(ItemSchema):
    episode_number: int | None = None


class TVShow(Item):
    if TYPE_CHECKING:
        seasons: models.QuerySet["TVSeason"]
    schema = TVShowSchema
    child_class = "TVSeason"
    category = ItemCategory.TV
    url_path = "tv"
    imdb = PrimaryLookupIdDescriptor(IdType.IMDB)
    tmdb_tv = PrimaryLookupIdDescriptor(IdType.TMDB_TV)
    imdb = PrimaryLookupIdDescriptor(IdType.IMDB)
    season_count = models.IntegerField(
        verbose_name=_("number of seasons"), null=True, blank=True
    )
    episode_count = models.PositiveIntegerField(
        verbose_name=_("number of episodes"), null=True, blank=True
    )

    METADATA_COPY_LIST = [
        "localized_title",
        "season_count",
        "orig_title",
        "director",
        "playwright",
        "actor",
        "localized_description",
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
        _("show time"),
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
                        "title": _("Date"),
                        "placeholder": _("YYYY-MM-DD"),
                    },
                    "region": {
                        "type": "string",
                        "title": _("Region or Event"),
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
    single_episode_length = jsondata.IntegerField(
        verbose_name=_("episode length"), null=True, blank=True
    )
    season_number = jsondata.IntegerField(
        null=True, blank=True
    )  # TODO remove after migration
    duration = jsondata.CharField(
        blank=True, max_length=200
    )  # TODO remove after migration

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.IMDB,
            IdType.TMDB_TV,
            IdType.DoubanMovie,
        ]
        return [(i.value, i.label) for i in id_types]

    @cached_property
    def all_seasons(self):
        return (
            self.seasons.all()
            .order_by("season_number")
            .filter(is_deleted=False, merged_to_item=None)
        )

    @property
    def child_items(self):
        return self.all_seasons

    @property
    def season_uuids(self):
        return [x.uuid for x in self.all_seasons]

    def get_season_count(self):
        return self.season_count or self.seasons.all().count()

    def to_indexable_titles(self) -> list[str]:
        titles = [t["text"] for t in self.localized_title if t["text"]]
        titles += [self.orig_title] if self.orig_title else []
        return list(set(titles))

    def to_indexable_people(self) -> list[str]:
        return (self.director or []) + (self.actor or []) + (self.playwright or [])


class TVSeason(Item):
    if TYPE_CHECKING:
        episodes: models.QuerySet["TVEpisode"]
    schema = TVSeasonSchema
    category = ItemCategory.TV
    url_path = "tv/season"
    child_class = "TVEpisode"
    douban_movie = PrimaryLookupIdDescriptor(IdType.DoubanMovie)
    imdb = PrimaryLookupIdDescriptor(IdType.IMDB)
    tmdb_tvseason = PrimaryLookupIdDescriptor(IdType.TMDB_TVSeason)
    show = models.ForeignKey(
        TVShow, null=True, on_delete=models.SET_NULL, related_name="seasons"
    )
    season_number = models.PositiveIntegerField(
        verbose_name=_("season number"), null=True
    )
    episode_count = models.PositiveIntegerField(
        verbose_name=_("number of episodes"), null=True
    )

    METADATA_COPY_LIST = [
        "localized_title",
        "season_number",
        "episode_count",
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
        "single_episode_length",
        "localized_description",
    ]
    orig_title = jsondata.CharField(
        verbose_name=_("original title"), blank=True, default="", max_length=500
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
        _("show time"),
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
    site = jsondata.URLField(
        verbose_name=_("website"), blank=True, default="", max_length=200
    )
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
    language = jsondata.ArrayField(
        verbose_name=_("language"),
        base_field=models.CharField(blank=True, default="", max_length=100),
        null=True,
        blank=True,
        default=list,
        schema={
            "type": "list",
            "items": {"type": "string", "choices": LANGUAGE_CHOICES_JSONFORM},
        },
    )
    year = jsondata.IntegerField(verbose_name=_("year"), null=True, blank=True)
    single_episode_length = jsondata.IntegerField(
        verbose_name=_("episode length"), null=True, blank=True
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
            IdType.Bangumi,
        ]
        return [(i.value, i.label) for i in id_types]

    @cached_property
    def display_title(self):
        """
        returns season title for display:
         - "Season Title" if it's not a bare "Season X"
         - "Show Title" if it's the only season
         - "Show Title Season X" with some localization
        """
        s = super().display_title
        if self.parent_item:
            if (
                RE_LOCALIZED_SEASON_NUMBERS.sub("", s) == ""
                or s == self.parent_item.display_title
            ):
                if self.parent_item.get_season_count() == 1:
                    return self.parent_item.display_title
                elif self.season_number:
                    return _("{show_title} Season {season_number}").format(
                        show_title=self.parent_item.display_title,
                        season_number=localize_number(self.season_number),
                    )
                else:
                    return f"{self.parent_item.display_title} {s}"
            elif self.parent_item.display_title not in s:
                return f"{self.parent_item.display_title} ({s})"
        return s

    @cached_property
    def additional_title(self) -> list[str]:
        title = self.display_title
        return uniq(
            [
                t["text"]
                for t in self.localized_title
                if t["text"] != title
                and RE_LOCALIZED_SEASON_NUMBERS.sub("", t["text"]) != ""
            ]
        )

    def to_indexable_titles(self) -> list[str]:
        titles = [t["text"] for t in self.localized_title if t["text"]]
        titles += [self.orig_title] if self.orig_title else []
        titles += self.parent_item.to_indexable_titles() if self.parent_item else []
        return list(set(titles))

    def to_indexable_people(self) -> list[str]:
        return (self.director or []) + (self.actor or []) + (self.playwright or [])

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

    @cached_property
    def all_episodes(self):
        return self.episodes.all().order_by("episode_number")

    @property
    def parent_item(self) -> TVShow | None:  # type:ignore
        return self.show

    def set_parent_item(self, value: TVShow | None):  # type:ignore
        self.show = value

    @property
    def child_items(self):
        return self.episodes.all()

    @property
    def episode_uuids(self):
        return [x.uuid for x in self.all_episodes]


class TVEpisode(Item):
    schema = TVEpisodeSchema
    category = ItemCategory.TV
    url_path = "tv/episode"
    season = models.ForeignKey(
        TVSeason, null=True, on_delete=models.SET_NULL, related_name="episodes"
    )
    season_number = jsondata.IntegerField(null=True)
    episode_number = models.PositiveIntegerField(null=True)
    imdb = PrimaryLookupIdDescriptor(IdType.IMDB)
    METADATA_COPY_LIST = ["title", "brief", "season_number", "episode_number"]

    @property
    def display_title(self):
        return (
            _("{season_title} E{episode_number}")
            .format(
                season_title=self.season.display_title if self.season else "",
                episode_number=self.episode_number,
            )
            .strip()
        )

    @property
    def parent_item(self) -> TVSeason | None:  # type:ignore
        return self.season

    def set_parent_item(self, value: TVSeason | None):  # type:ignore
        self.season = value

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.IMDB,
            IdType.TMDB_TVEpisode,
        ]
        return [(i.value, i.label) for i in id_types]

    def update_linked_items_from_external_resource(self, resource: ExternalResource):
        for w in resource.required_resources:
            if w["model"] == "TVSeason":
                p = ExternalResource.objects.filter(
                    id_type=w["id_type"], id_value=w["id_value"]
                ).first()
                if p and p.item:
                    self.season = p.item
