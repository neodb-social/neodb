from datetime import datetime
from typing import TYPE_CHECKING

from django.db import models
from django.utils.translation import gettext_lazy as _
from ninja import Field

from .common import (
    LIST_OF_ONE_PLUS_STR_SCHEMA,
    GenreListField,
    LanguageListField,
    jsondata,
)
from .creator import VerifiedCreator
from .item import (
    BaseSchema,
    IdType,
    Item,
    ItemCategory,
    ItemInSchema,
    ItemType,
)
from .people import PeopleRole


class PodcastInSchema(ItemInSchema):
    genre: list[str]
    host: list[str]
    language: list[str]
    official_site: str | None = None
    # hosts is deprecated
    hosts: list[str] = Field(deprecated=True, alias="host")

    @staticmethod
    def resolve_host(obj: "Podcast") -> list[str]:
        return obj.credit_names_by_role("host")


class PodcastSchema(PodcastInSchema, BaseSchema):
    pass


class PodcastEpisodeInSchema(ItemInSchema):
    guid: str | None = None
    pub_date: datetime | None = None
    media_url: str | None = None
    link: str | None = None
    duration: int | None = None


class PodcastEpisodeSchema(PodcastEpisodeInSchema, BaseSchema):
    pass


class Podcast(Item):
    if TYPE_CHECKING:
        episodes: models.QuerySet["PodcastEpisode"]
    schema = PodcastSchema
    category = ItemCategory.Podcast
    type = ItemType.Podcast
    child_class = "PodcastEpisode"
    url_path = "podcast"

    available_roles = [
        PeopleRole.HOST,
        PeopleRole.PRODUCER,
    ]
    CREDIT_FIELD_MAPPING = {
        "host": "host",
    }
    # apple_podcast = PrimaryLookupIdDescriptor(IdType.ApplePodcast)
    # ximalaya = LookupIdDescriptor(IdType.Ximalaya)
    # xiaoyuzhou = LookupIdDescriptor(IdType.Xiaoyuzhou)
    genre = GenreListField(ItemCategory.Podcast)

    language = LanguageListField()

    host = jsondata.ArrayField(
        verbose_name=_("host"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=False,
        blank=False,
        default=list,
        schema=LIST_OF_ONE_PLUS_STR_SCHEMA,
    )

    official_site = jsondata.CharField(
        verbose_name=_("website"), max_length=1000, null=True, blank=True
    )

    # Polling tracking (used by PodcastUpdater); stored in Item.metadata
    feed_last_fetched_at = jsondata.DateTimeField(null=True, blank=True, default=None)
    feed_etag = jsondata.CharField(max_length=255, default="", blank=True)
    feed_last_modified = jsondata.CharField(max_length=255, default="", blank=True)
    feed_consecutive_failures = jsondata.IntegerField(default=0, blank=True)

    METADATA_COPY_LIST = [
        # "title",
        # "brief",
        "localized_title",
        "language",
        "host",
        "genre",
        "official_site",
        "localized_description",
    ]

    @property
    def host_names(self) -> list[str]:
        return self.credit_names_by_role("host")

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.RSS,
        ]
        return [(i.value, i.label) for i in id_types]

    @classmethod
    def verified_originals(cls) -> models.QuerySet["Podcast"]:
        """Podcasts with a verified creator, most recently verified first.

        These are the shows behind the discover page's "original episodes"
        shelf. A show may carry several verified-creator claims, so it is
        ranked by the newest of those claims and appears only once.

        A show is hidden entirely if any of its verified creators is a
        restricted (limited or blocked) identity, so moderated creators do
        not surface on discover.
        """
        from takahe.models import Identity

        # Restriction lives on the Takahe identity (separate db, shared pk),
        # so materialize the restricted ids rather than joining across dbs.
        restricted_ids = list(
            Identity.objects.filter(
                restriction__gt=Identity.Restriction.none
            ).values_list("pk", flat=True)
        )
        qs = (
            cls.objects.filter(
                is_deleted=False,
                merged_to_item__isnull=True,
            )
            .annotate(
                verified_at=models.Max(
                    "verified_creators__created_time",
                    filter=models.Q(
                        verified_creators__state=VerifiedCreator.State.VERIFIED
                    ),
                )
            )
            .filter(verified_at__isnull=False)
        )
        if restricted_ids:
            qs = qs.exclude(
                verified_creators__state=VerifiedCreator.State.VERIFIED,
                verified_creators__owner_id__in=restricted_ids,
            )
        return qs.order_by("-verified_at", "-pk")

    @property
    def recent_episodes(self):
        return self.episodes.all().order_by("-pub_date")[:10]

    @property
    def feed_url(self):
        if (
            self.primary_lookup_id_type != IdType.RSS
            or not self.primary_lookup_id_value
        ):
            return None
        # https to match RSS.id_to_url; fetch_feed_with_metadata falls back to
        # http for feeds that are not served over https
        return f"https://{self.primary_lookup_id_value}"

    @property
    def child_items(self):
        return self.episodes.filter(is_deleted=False, merged_to_item=None)

    @property
    def child_item_ids(self) -> list[int]:
        # The default implementation evaluates ``child_items`` which forces a
        # JOIN with catalog_item to filter on is_deleted / merged_to_item_id;
        # for podcasts with many episodes this becomes a slow query
        # (Sentry: EGGPLANT-1BH). Split into two simple index lookups so the
        # FK scan and the PK-bounded filter happen independently.
        raw_ids = list(self.episodes.values_list("id", flat=True))
        if not raw_ids:
            return []
        return list(
            Item.objects.filter(
                id__in=raw_ids,
                is_deleted=False,
                merged_to_item__isnull=True,
            ).values_list("id", flat=True)
        )

    def is_deletable(self):
        # override is_deletable() and allow delete podcast with episodes
        return (
            not self.is_deleted
            and not self.merged_to_item_id
            and not self.merged_from_items.exists()
        )

    def to_indexable_doc(self):
        d = super().to_indexable_doc()
        d["genre"] = self.genre or []
        return d

    def to_schema_org(self):
        data = super().to_schema_org()
        data["@type"] = "PodcastSeries"

        if self.feed_url:
            data["webFeed"] = self.feed_url

        if self.genre:
            data["genre"] = self.genre

        hosts = self.credit_names_by_role("host")
        if hosts:
            data["author"] = [{"@type": "Person", "name": person} for person in hosts]

        if self.official_site:
            data["sameAs"] = self.official_site

        return data


class PodcastEpisode(Item):
    schema = PodcastEpisodeSchema
    category = ItemCategory.Podcast
    type = ItemType.PodcastEpisode
    url_path = "podcast/episode"

    available_roles = [
        PeopleRole.HOST,
        PeopleRole.PRODUCER,
    ]
    # uid = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    program = models.ForeignKey(Podcast, models.CASCADE, related_name="episodes")
    guid = models.CharField(null=True, max_length=1000)
    pub_date = models.DateTimeField(
        verbose_name=_("date of publication"), help_text="yyyy/mm/dd hh:mm"
    )
    media_url = models.CharField(null=True, max_length=1000)
    # title = models.CharField(default="", max_length=1000)
    # description = models.TextField(null=True)
    description_html = models.TextField(null=True)
    link = models.CharField(null=True, max_length=1000)
    cover_url = models.CharField(null=True, max_length=1000)
    duration = models.PositiveIntegerField(null=True)

    METADATA_COPY_LIST = [
        "title",
        "brief",
        "pub_date",
    ]

    @property
    def parent_item(self) -> Podcast | None:
        return self.program

    def set_parent_item(self, value: Podcast | None):  # type:ignore
        self.program = value

    @property
    def display_title(self) -> str:
        return f"{self.program.title} - {self.title}" if self.program else self.title

    def to_indexable_doc(self):
        return {}  # no index for PodcastEpisode, for now

    def to_schema_org(self):
        data = super().to_schema_org()
        data["@type"] = "PodcastEpisode"

        if self.program:
            data["partOfSeries"] = {
                "@type": "PodcastSeries",
                "name": self.program.display_title,
                "url": self.program.absolute_url,
            }

        if self.pub_date:
            data["datePublished"] = self.pub_date.isoformat()

        if self.media_url:
            data["associatedMedia"] = {
                "@type": "MediaObject",
                "contentUrl": self.media_url,
            }

        if self.duration:
            hours = self.duration // 3600
            minutes = (self.duration % 3600) // 60
            seconds = self.duration % 60
            data["duration"] = f"PT{hours}H{minutes}M{seconds}S"

        if self.link:
            data["sameAs"] = self.link

        return data

    @property
    def cover_image_url(self) -> str | None:
        return self.cover_url or (
            self.program.cover_image_url if self.program else None
        )

    def get_url_with_position(self, position: int | str | None = None):
        return (
            self.url
            if position is None or position == ""
            else f"{self.url}?position={position}"
        )

    @classmethod
    def lookup_id_type_choices(cls):
        return []

    class Meta:
        indexes = [models.Index(fields=["program", "pub_date"])]
        unique_together = [["program", "guid"]]
