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
from loguru import logger
from ninja import Field, Schema

from common.models import (
    duration_to_seconds,
    partial_date_to_int,
    year_of_partial_date,
)
from common.models.lang import RE_LOCALIZED_SEASON_NUMBERS, localize_number
from common.models.misc import uniq

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
    ExternalResource,
    IdType,
    Item,
    ItemCategory,
    ItemInSchema,
    ItemSchema,
    ItemType,
    PrimaryLookupIdDescriptor,
)
from .people import PeopleRole
from .utils import normalize_legacy_video_metadata


class _TVCreditResolverMixin(Schema):
    @staticmethod
    def resolve_director(obj: "TVShow | TVSeason") -> list[str]:
        return obj.credit_names_by_role("director")

    @staticmethod
    def resolve_playwright(obj: "TVShow | TVSeason") -> list[str]:
        return obj.credit_names_by_role("playwright")

    @staticmethod
    def resolve_actor(obj: "TVShow | TVSeason") -> list[str]:
        return obj.credit_names_by_role("actor")

    @staticmethod
    def resolve_producer(obj: "TVShow | TVSeason") -> list[str]:
        return obj.credit_names_by_role("producer")


class TVShowInSchema(VideoFieldsResolverMixin, _TVCreditResolverMixin, ItemInSchema):
    season_count: int | None = None
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
    episode_count: int | None = None
    season_uuids: list[str]
    # area and showtime are deprecated
    area: list[str] = Field(
        [], deprecated="Use `origin_country` (ISO 3166-1 alpha-2) instead."
    )
    showtime: list[dict] = Field([], deprecated="Use `release_date` instead.")


class TVShowSchema(TVShowInSchema, BaseSchema):
    imdb: str | None = None
    # seasons: list['TVSeason']
    pass


class TVSeasonInSchema(VideoFieldsResolverMixin, _TVCreditResolverMixin, ItemInSchema):
    season_number: int | None = None
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
    episode_count: int | None = None
    episode_uuids: list[str]
    # area and showtime are deprecated
    area: list[str] = Field(
        [], deprecated="Use `origin_country` (ISO 3166-1 alpha-2) instead."
    )
    showtime: list[dict] = Field([], deprecated="Use `release_date` instead.")


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
    type = ItemType.TVShow
    url_path = "tv"

    available_roles = [
        PeopleRole.DIRECTOR,
        PeopleRole.PLAYWRIGHT,
        PeopleRole.ACTOR,
        PeopleRole.PRODUCER,
        PeopleRole.PRODUCTION_COMPANY,
        PeopleRole.DISTRIBUTOR,
    ]
    CREDIT_FIELD_MAPPING = {
        "director": "director",
        "playwright": "playwright",
        "actor": "actor",
        "producer": "producer",
    }
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
        "producer",
        "localized_description",
        "genre",
        "release_date",
        "site",
        "origin_country",
        "language",
        "length",
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
    producer = jsondata.JSONField(
        verbose_name=_("producer"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    genre = GenreListField(ItemCategory.TV)
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

    # collected but not displayed or exposed in API;
    # fate undecided, likely to be deprecated
    single_episode_length = jsondata.IntegerField(
        verbose_name=_("episode length"),
        null=True,
        blank=True,
        help_text=_("seconds"),
    )
    season_number = jsondata.IntegerField(
        null=True, blank=True
    )  # TODO remove after migration
    length = jsondata.IntegerField(null=True, blank=True)  # TODO remove after migration

    @property
    def year(self) -> int | None:
        return year_of_partial_date(self.release_date)

    @property
    def official_site(self) -> str | None:
        return self.site

    @classmethod
    def normalize_legacy_metadata(cls, metadata: dict) -> None:
        super().normalize_legacy_metadata(metadata)
        normalize_legacy_video_metadata(metadata)

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
        dt = partial_date_to_int(self.release_date)
        d["date"] = [dt] if dt else []
        d["genre"] = self.genre or []
        return d

    def to_schema_org(self):
        data = super().to_schema_org()
        data["@type"] = "TVSeries"

        if self.orig_title and self.orig_title != self.display_title:
            data["alternateName"] = self.orig_title

        if self.genre:
            data["genre"] = self.genre

        if self.language:
            data["inLanguage"] = self.language[0]

        actors = self.credit_names_by_role("actor")
        if actors:
            data["actor"] = [{"@type": "Person", "name": person} for person in actors]

        directors = self.credit_names_by_role("director")
        if directors:
            data["director"] = [
                {"@type": "Person", "name": person} for person in directors
            ]

        playwrights = self.credit_names_by_role("playwright")
        if playwrights:
            data["creator"] = [
                {"@type": "Person", "name": person} for person in playwrights
            ]

        if self.release_date:
            data["datePublished"] = self.release_date

        if self.season_count:
            data["numberOfSeasons"] = self.season_count

        if self.episode_count:
            data["numberOfEpisodes"] = self.episode_count

        episode_length = duration_to_seconds(self.single_episode_length)
        if episode_length:
            data["timeRequired"] = f"PT{episode_length // 60}M"

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
    type = ItemType.TVSeason
    category = ItemCategory.TV
    url_path = "tv/season"
    child_class = "TVEpisode"

    available_roles = [
        PeopleRole.DIRECTOR,
        PeopleRole.PLAYWRIGHT,
        PeopleRole.ACTOR,
        PeopleRole.PRODUCER,
        PeopleRole.PRODUCTION_COMPANY,
        PeopleRole.DISTRIBUTOR,
    ]
    CREDIT_FIELD_MAPPING = {
        "director": "director",
        "playwright": "playwright",
        "actor": "actor",
        "producer": "producer",
    }
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
        "producer",
        "genre",
        "release_date",
        "site",
        "origin_country",
        "language",
        "length",
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
    producer = jsondata.JSONField(
        verbose_name=_("producer"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    genre = GenreListField(ItemCategory.TV)
    release_date = jsondata.CharField(
        verbose_name=_("release date"),
        null=True,
        blank=True,
        max_length=10,
        help_text=_("YYYY, YYYY-MM or YYYY-MM-DD"),
    )
    site = jsondata.URLField(
        verbose_name=_("website"), blank=True, default="", max_length=200
    )
    origin_country = CountryListField()
    language = LanguageListField()
    # collected but not displayed or exposed in API;
    # fate undecided, likely to be deprecated
    single_episode_length = jsondata.IntegerField(
        verbose_name=_("episode length"),
        null=True,
        blank=True,
        help_text=_("seconds"),
    )
    length = jsondata.IntegerField(null=True, blank=True)  # TODO remove after migration

    @property
    def year(self) -> int | None:
        return year_of_partial_date(self.release_date)

    @property
    def official_site(self) -> str | None:
        return self.site

    @classmethod
    def normalize_legacy_metadata(cls, metadata: dict) -> None:
        super().normalize_legacy_metadata(metadata)
        normalize_legacy_video_metadata(metadata)

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
        dt = partial_date_to_int(self.release_date)
        d["date"] = [dt] if dt else []
        d["genre"] = self.genre or []
        return d

    def to_schema_org(self):
        data = super().to_schema_org()
        data["@type"] = "TVSeason"

        if self.orig_title and self.orig_title != self.display_title:
            data["alternateName"] = self.orig_title

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
            data["inLanguage"] = self.language[0]

        actors = self.credit_names_by_role("actor")
        if actors:
            data["actor"] = [{"@type": "Person", "name": person} for person in actors]

        directors = self.credit_names_by_role("director")
        if directors:
            data["director"] = [
                {"@type": "Person", "name": person} for person in directors
            ]

        playwrights = self.credit_names_by_role("playwright")
        if playwrights:
            data["creator"] = [
                {"@type": "Person", "name": person} for person in playwrights
            ]

        if self.release_date:
            data["datePublished"] = self.release_date

        episode_length = duration_to_seconds(self.single_episode_length)
        if episode_length:
            data["timeRequired"] = f"PT{episode_length // 60}M"

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
    type = ItemType.TVEpisode
    url_path = "tv/episode"

    available_roles = [
        PeopleRole.DIRECTOR,
        PeopleRole.PLAYWRIGHT,
        PeopleRole.ACTOR,
        PeopleRole.PRODUCER,
    ]
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
        data = super().to_schema_org()
        data["@type"] = "TVEpisode"

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
