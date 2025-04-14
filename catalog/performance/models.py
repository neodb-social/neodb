from functools import cached_property
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _
from ninja import Schema

from catalog.common import (
    ExternalResource,
    IdType,
    Item,
    ItemCategory,
    ItemSchema,
    jsondata,
)
from catalog.common.models import LanguageListField


class CrewMemberSchema(Schema):
    name: str
    role: str | None


class PerformanceSchema(ItemSchema):
    orig_title: str | None = None
    other_title: list[str]
    genre: list[str]
    language: list[str]
    opening_date: str | None = None
    closing_date: str | None = None
    director: list[str]
    playwright: list[str]
    orig_creator: list[str]
    composer: list[str]
    choreographer: list[str]
    performer: list[str]
    actor: list[CrewMemberSchema]
    crew: list[CrewMemberSchema]
    official_site: str | None = None


class PerformanceProductionSchema(ItemSchema):
    orig_title: str | None = None
    other_title: list[str]
    language: list[str]
    opening_date: str | None = None
    closing_date: str | None = None
    director: list[str]
    playwright: list[str]
    orig_creator: list[str]
    composer: list[str]
    choreographer: list[str]
    performer: list[str]
    actor: list[CrewMemberSchema]
    crew: list[CrewMemberSchema]
    official_site: str | None = None


_CREW_SCHEMA = {
    "type": "list",
    "items": {
        "type": "dict",
        "keys": {
            "name": {"type": "string", "title": _("name")},
            "role": {"type": "string", "title": _("role")},
        },
        "required": ["role", "name"],
    },
    "uniqueItems": True,
}

_ACTOR_SCHEMA = {
    "type": "list",
    "items": {
        "type": "dict",
        "keys": {
            "name": {
                "type": "string",
                "title": _("name"),
                "placeholder": _("required"),
            },
            "role": {
                "type": "string",
                "title": _("role"),
                "placeholder": _("optional"),
            },
        },
        "required": ["name"],
    },
    "uniqueItems": True,
}


def _crew_by_role(crew):
    roles = set([c["role"] for c in crew if c.get("role")])
    r = {key: [] for key in roles}
    for c in crew:
        r[c["role"]].append(c["name"])
    return r


class Performance(Item):
    if TYPE_CHECKING:
        productions: models.QuerySet["PerformanceProduction"]
    schema = PerformanceSchema
    child_class = "PerformanceProduction"
    category = ItemCategory.Performance
    url_path = "performance"
    orig_title = jsondata.CharField(
        verbose_name=_("original name"), blank=True, max_length=500
    )
    other_title = jsondata.ArrayField(
        verbose_name=_("other title"),
        base_field=models.CharField(blank=False, default="", max_length=200),
        null=False,
        blank=True,
        default=list,
    )
    genre = jsondata.ArrayField(
        verbose_name=_("genre"),
        base_field=models.CharField(blank=False, default="", max_length=200),
        null=False,
        blank=False,
        default=list,
    )
    language = LanguageListField()
    director = jsondata.ArrayField(
        verbose_name=_("director"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    playwright = jsondata.ArrayField(
        verbose_name=_("playwright"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    orig_creator = jsondata.ArrayField(
        verbose_name=_("original creator"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    composer = jsondata.ArrayField(
        verbose_name=_("composer"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    choreographer = jsondata.ArrayField(
        verbose_name=_("choreographer"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    actor = jsondata.JSONField(
        verbose_name=_("actor"),
        null=False,
        blank=True,
        default=list,
        schema=_ACTOR_SCHEMA,
    )
    performer = jsondata.ArrayField(
        verbose_name=_("performer"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    troupe = jsondata.ArrayField(
        verbose_name=_("troupe"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    crew = jsondata.JSONField(
        verbose_name=_("crew"),
        null=False,
        blank=True,
        default=list,
        schema=_CREW_SCHEMA,
    )
    location = jsondata.ArrayField(
        verbose_name=_("theater"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    opening_date = jsondata.CharField(
        verbose_name=_("opening date"), max_length=100, null=True, blank=True
    )
    closing_date = jsondata.CharField(
        verbose_name=_("closing date"), max_length=100, null=True, blank=True
    )
    official_site = jsondata.CharField(
        verbose_name=_("website"), max_length=1000, null=True, blank=True
    )
    METADATA_COPY_LIST = [
        # "title",
        # "brief",
        "localized_title",
        "localized_description",
        "orig_title",
        # "other_title",
        "genre",
        "language",
        "opening_date",
        "closing_date",
        "troupe",
        "location",
        "director",
        "playwright",
        "orig_creator",
        "composer",
        "choreographer",
        "actor",
        "performer",
        "crew",
        "official_site",
    ]


    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.DoubanDrama,
            IdType.Bangumi,
        ]
        return [(i.value, i.label) for i in id_types]

    @cached_property
    def all_productions(self):
        return (
            self.productions.all()
            .order_by("metadata__opening_date", "title")
            .filter(is_deleted=False, merged_to_item=None)
        )

    @cached_property
    def crew_by_role(self):
        return _crew_by_role(self.crew)

    @property
    def child_items(self):
        return self.all_productions


class PerformanceProduction(Item):
    schema = PerformanceProductionSchema
    category = ItemCategory.Performance
    url_path = "performance/production"
    show = models.ForeignKey(
        Performance, null=True, on_delete=models.SET_NULL, related_name="productions"
    )
    orig_title = jsondata.CharField(
        verbose_name=_("original title"), blank=True, default="", max_length=500
    )
    other_title = jsondata.ArrayField(
        verbose_name=_("other title"),
        base_field=models.CharField(blank=False, default="", max_length=200),
        null=False,
        blank=True,
        default=list,
    )
    language = LanguageListField()
    director = jsondata.ArrayField(
        verbose_name=_("director"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    playwright = jsondata.ArrayField(
        verbose_name=_("playwright"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    orig_creator = jsondata.ArrayField(
        verbose_name=_("original creator"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    composer = jsondata.ArrayField(
        verbose_name=_("composer"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    choreographer = jsondata.ArrayField(
        verbose_name=_("choreographer"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    actor = jsondata.JSONField(
        verbose_name=_("actor"),
        null=False,
        blank=True,
        default=list,
        schema=_ACTOR_SCHEMA,
    )
    performer = jsondata.ArrayField(
        verbose_name=_("performer"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    troupe = jsondata.ArrayField(
        verbose_name=_("troupe"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    crew = jsondata.JSONField(
        verbose_name=_("crew"),
        null=False,
        blank=True,
        default=list,
        schema=_CREW_SCHEMA,
    )
    location = jsondata.ArrayField(
        verbose_name=_("theater"),
        base_field=models.CharField(blank=False, default="", max_length=500),
        null=False,
        blank=True,
        default=list,
    )
    opening_date = jsondata.CharField(
        verbose_name=_("opening date"), max_length=100, null=True, blank=False
    )
    closing_date = jsondata.CharField(
        verbose_name=_("closing date"), max_length=100, null=True, blank=True
    )
    official_site = jsondata.CharField(
        verbose_name=_("website"), max_length=1000, null=True, blank=True
    )
    METADATA_COPY_LIST = [
        "localized_title",
        "localized_description",
        # "title",
        # "brief",
        "orig_title",
        # "other_title",
        "language",
        "opening_date",
        "closing_date",
        "troupe",
        "location",
        "director",
        "playwright",
        "orig_creator",
        "composer",
        "choreographer",
        "actor",
        "performer",
        "crew",
        "official_site",
    ]

    @property
    def parent_item(self) -> Performance | None:  # type:ignore
        return self.show

    def set_parent_item(self, value: Performance | None):  # type:ignore
        self.show = value

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.DoubanDramaVersion,
        ]
        return [(i.value, i.label) for i in id_types]

    @property
    def display_title(self):
        return (
            f"{self.show.display_title if self.show else '♢'} {super().display_title}"
        )

    @property
    def cover_image_url(self) -> str | None:
        return (
            self.cover.url  # type:ignore
            if self.cover and self.cover != settings.DEFAULT_ITEM_COVER
            else self.show.cover_image_url
            if self.show
            else None
        )

    def update_linked_items_from_external_resource(self, resource: ExternalResource):
        for r in resource.required_resources:
            if r["model"] == "Performance":
                res = ExternalResource.objects.filter(
                    id_type=r["id_type"], id_value=r["id_value"]
                ).first()
                if res and res.item:
                    self.show = res.item

    @cached_property
    def crew_by_role(self):
        return _crew_by_role(self.crew)
