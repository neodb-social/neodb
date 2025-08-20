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
from typing import TYPE_CHECKING, Any

from django.db import models
from django.utils.translation import gettext_lazy as _
from loguru import logger

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
    LIST_OF_STR_SCHEMA,
    LanguageListField,
)
from common.models.lang import RE_LOCALIZED_SEASON_NUMBERS, localize_number
from common.models.misc import int_, uniq


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
        """Generate Schema.org structured data for TV show."""
        data: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "TVSeries",
            "name": self.display_title,
            "url": self.absolute_url,
        }

        if self.orig_title and self.orig_title != self.display_title:
            data["alternateName"] = self.orig_title

        if self.display_description:
            data["description"] = self.display_description

        if self.has_cover():
            data["image"] = self.cover_image_url

        if self.genre:
            data["genre"] = self.genre

        if self.language:
            data["inLanguage"] = self.language[0]  # type:ignore

        if self.actor:
            data["actor"] = [
                {"@type": "Person", "name": person} for person in self.actor
            ]

        if self.director:
            data["director"] = [
                {"@type": "Person", "name": person} for person in self.director
            ]

        if self.playwright:
            data["creator"] = [
                {"@type": "Person", "name": person} for person in self.playwright
            ]

        if self.year:
            data["datePublished"] = str(self.year)

        if self.season_count:
            data["numberOfSeasons"] = self.season_count

        if self.episode_count:
            data["numberOfEpisodes"] = self.episode_count

        if self.single_episode_length:
            data["timeRequired"] = f"PT{self.single_episode_length}M"

        if self.site:
            data["sameAs"] = self.site

        if self.imdb:
            data["sameAs"] = f"https://www.imdb.com/title/{self.imdb}/"

        if self.all_seasons:
            data["containsSeason"] = [
                {
                    "@type": "TVSeason",
                    "seasonNumber": season.season_number,
                    "name": season.display_title,
                    "url": season.absolute_url,
                }
                for season in self.all_seasons
                if season.season_number
            ]

        return data


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
    language = LanguageListField()
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

    def to_indexable_doc(self):
        d = super().to_indexable_doc()
        d["people"] = (
            (self.director or []) + (self.actor or []) + (self.playwright or [])
        )
        dt = int_(self.year) * 10000
        d["date"] = [dt] if dt else []
        d["genre"] = self.genre or []  # type: ignore
        return d

    def to_schema_org(self):
        """Generate Schema.org structured data for TV season."""
        data = {
            "@context": "https://schema.org",
            "@type": "TVSeason",
            "name": self.display_title,
            "url": self.absolute_url,
        }

        if self.orig_title and self.orig_title != self.display_title:
            data["alternateName"] = self.orig_title

        if self.display_description:
            data["description"] = self.display_description

        if self.has_cover():
            data["image"] = self.cover_image_url

        if self.season_number is not None:
            data["seasonNumber"] = self.season_number

        if self.episode_count:
            data["numberOfEpisodes"] = self.episode_count

        if self.show:
            data["partOfSeries"] = {
                "@type": "TVSeries",
                "name": self.show.display_title,
                "url": self.show.absolute_url,
            }

        if self.genre:
            data["genre"] = self.genre

        if self.language:
            data["inLanguage"] = self.language[0]  # type:ignore

        if self.actor:
            data["actor"] = [
                {"@type": "Person", "name": person} for person in self.actor
            ]

        if self.director:
            data["director"] = [
                {"@type": "Person", "name": person} for person in self.director
            ]

        if self.playwright:
            data["creator"] = [
                {"@type": "Person", "name": person} for person in self.playwright
            ]

        if self.year:
            data["datePublished"] = str(self.year)

        if self.single_episode_length:
            data["timeRequired"] = f"PT{self.single_episode_length}M"

        if self.all_episodes:
            data["episode"] = [
                {
                    "@type": "TVEpisode",
                    "episodeNumber": episode.episode_number,
                    "name": episode.display_title,
                    "url": episode.absolute_url,
                }
                for episode in self.all_episodes
                if episode.episode_number
            ]

        return data

    def process_fetched_item(self, fetched, link_type):
        if (
            link_type == ExternalResource.LinkType.PARENT
            and isinstance(fetched, TVShow)
            and self.show != fetched
        ):
            self.show = fetched
            return True
        return False

    def all_seasons(self):
        return self.show.all_seasons if self.show else []

    @cached_property
    def all_episodes(self):
        return self.episodes.all().order_by("episode_number")

    @property
    def parent_item(self) -> TVShow | None:
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
    def parent_item(self) -> TVSeason | None:
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

    def process_fetched_item(self, fetched, link_type):
        logger.debug(f"updating linked items for TVEpisode {self} from {fetched}")
        if (
            link_type == ExternalResource.LinkType.PARENT
            and isinstance(fetched, TVSeason)
            and self.season != fetched
        ):
            self.season = fetched
            return True
        return False

    def to_indexable_doc(self):
        return {}  # no index for TVEpisode, for now

    def to_schema_org(self):
        """Generate Schema.org structured data for TV episode."""
        data = {
            "@context": "https://schema.org",
            "@type": "TVEpisode",
            "name": self.title,
            "url": self.absolute_url,
        }

        if self.season:
            data["partOfSeason"] = {
                "@type": "TVSeason",
                "name": self.season.display_title,
                "url": self.season.absolute_url,
            }

            if self.season.show:
                data["partOfSeries"] = {
                    "@type": "TVSeries",
                    "name": self.season.show.display_title,
                    "url": self.season.show.absolute_url,
                }

        if self.display_description:
            data["description"] = self.display_description

        if self.has_cover():
            data["image"] = self.cover_image_url

        if self.episode_number is not None:
            data["episodeNumber"] = self.episode_number

        if self.season_number is not None:
            data["partOfSeason"] = {
                "@type": "TVSeason",
                "seasonNumber": self.season_number,
            }

        if self.imdb:
            data["sameAs"] = f"https://www.imdb.com/title/{self.imdb}/"

        return data
