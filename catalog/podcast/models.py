from datetime import datetime
from typing import TYPE_CHECKING

from django.db import models
from django.utils.translation import gettext_lazy as _
from ninja import Field

from catalog.common import (
    BaseSchema,
    IdType,
    Item,
    ItemCategory,
    ItemInSchema,
    jsondata,
)
from catalog.common.models import (
    LIST_OF_ONE_PLUS_STR_SCHEMA,
    LanguageListField,
)


class PodcastInSchema(ItemInSchema):
    genre: list[str]
    host: list[str]
    language: list[str]
    official_site: str | None = None
    # hosts is deprecated
    hosts: list[str] = Field(deprecated=True, alias="host")


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
    child_class = "PodcastEpisode"
    url_path = "podcast"
    # apple_podcast = PrimaryLookupIdDescriptor(IdType.ApplePodcast)
    # ximalaya = LookupIdDescriptor(IdType.Ximalaya)
    # xiaoyuzhou = LookupIdDescriptor(IdType.Xiaoyuzhou)
    genre = jsondata.ArrayField(
        verbose_name=_("genre"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
    )

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

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.RSS,
        ]
        return [(i.value, i.label) for i in id_types]

    @property
    def recent_episodes(self):
        return self.episodes.all().order_by("-pub_date")[:10]

    @property
    def feed_url(self):
        if (
            self.primary_lookup_id_type != IdType.RSS
            and self.primary_lookup_id_value is None
        ):
            return None
        return f"http://{self.primary_lookup_id_value}"

    @property
    def child_items(self):
        return self.episodes.filter(is_deleted=False, merged_to_item=None)

    def can_soft_delete(self):
        # override can_soft_delete() and allow delete podcast with episodes
        return (
            not self.is_deleted
            and not self.merged_to_item_id
            and not self.merged_from_items.exists()
        )

    def to_indexable_doc(self):
        d = super().to_indexable_doc()
        d["people"] = self.host or []  # type:ignore
        d["genre"] = self.genre or []  # type:ignore
        return d


class PodcastEpisode(Item):
    schema = PodcastEpisodeSchema
    category = ItemCategory.Podcast
    url_path = "podcast/episode"
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
    def parent_item(self) -> Podcast | None:  # type:ignore
        return self.program

    def set_parent_item(self, value: Podcast | None):  # type:ignore
        self.program = value

    @property
    def display_title(self) -> str:
        return f"{self.program.title} - {self.title}" if self.program else self.title

    def to_indexable_doc(self):
        return {}  # no index for PodcastEpisode, for now

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
        index_together = [
            [
                "program",
                "pub_date",
            ]
        ]
        unique_together = [["program", "guid"]]
