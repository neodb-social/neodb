from django.db import models
from django.utils.translation import gettext_lazy as _

from catalog.common.models import *


class PodcastInSchema(ItemInSchema):
    genre: list[str]
    hosts: list[str]
    official_site: str | None = None


class PodcastSchema(PodcastInSchema, BaseSchema):
    pass


class Podcast(Item):
    category = ItemCategory.Podcast
    url_path = "podcast"
    demonstrative = _("这档播客")
    # apple_podcast = PrimaryLookupIdDescriptor(IdType.ApplePodcast)
    # ximalaya = LookupIdDescriptor(IdType.Ximalaya)
    # xiaoyuzhou = LookupIdDescriptor(IdType.Xiaoyuzhou)
    genre = jsondata.ArrayField(
        verbose_name=_("类型"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        null=True,
        blank=True,
        default=list,
    )

    hosts = jsondata.ArrayField(
        verbose_name=_("主播"),
        base_field=models.CharField(blank=True, default="", max_length=200),
        default=list,
    )

    official_site = jsondata.CharField(
        verbose_name=_("官方网站"), max_length=1000, null=True, blank=True
    )

    METADATA_COPY_LIST = [
        "title",
        "brief",
        "hosts",
        "genre",
        "official_site",
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
        return self.episodes.all()


class PodcastEpisode(Item):
    category = ItemCategory.Podcast
    url_path = "podcast/episode"
    demonstrative = _("这集节目")
    # uid = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    program = models.ForeignKey(Podcast, models.CASCADE, related_name="episodes")
    guid = models.CharField(null=True, max_length=1000)
    pub_date = models.DateTimeField()
    media_url = models.CharField(null=True, max_length=1000)
    # title = models.CharField(default="", max_length=1000)
    # description = models.TextField(null=True)
    description_html = models.TextField(null=True)
    link = models.CharField(null=True, max_length=1000)
    cover_url = models.CharField(null=True, max_length=1000)
    duration = models.PositiveIntegerField(null=True)

    @property
    def parent_item(self):
        return self.program

    @property
    def display_title(self):
        return f"{self.program.title} - {self.title}"

    @property
    def cover_image_url(self):
        return self.cover_url or self.program.cover_image_url

    def get_absolute_url_with_position(self, position=None):
        return (
            self.absolute_url
            if position is None or position == ""
            else f"{self.absolute_url}?position={position}"
        )

    class Meta:
        index_together = [
            [
                "program",
                "pub_date",
            ]
        ]
        unique_together = [["program", "guid"]]
