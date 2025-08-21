from functools import cached_property
from typing import TYPE_CHECKING, Any

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
from catalog.common.models import LIST_OF_STR_SCHEMA, ItemType, LanguageListField
from common.models.misc import datetime_


class CrewMemberSchema(Schema):
    name: str
    role: str | None


class PerformanceSchema(ItemSchema):
    orig_title: str | None = None
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
    type = ItemType.Performance
    child_class = "PerformanceProduction"
    category = ItemCategory.Performance
    url_path = "performance"
    orig_title = jsondata.CharField(
        verbose_name=_("original name"), blank=True, max_length=500
    )
    genre = jsondata.ArrayField(
        verbose_name=_("genre"),
        base_field=models.CharField(blank=False, default="", max_length=200),
        null=False,
        blank=False,
        default=list,
    )
    language = LanguageListField()
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
    orig_creator = jsondata.JSONField(
        verbose_name=_("original creator"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    composer = jsondata.JSONField(
        verbose_name=_("composer"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    choreographer = jsondata.JSONField(
        verbose_name=_("choreographer"),
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
        schema=_ACTOR_SCHEMA,
    )
    performer = jsondata.JSONField(
        verbose_name=_("performer"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    troupe = jsondata.JSONField(
        verbose_name=_("troupe"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    crew = jsondata.JSONField(
        verbose_name=_("crew"),
        null=False,
        blank=True,
        default=list,
        schema=_CREW_SCHEMA,
    )
    location = jsondata.JSONField(
        verbose_name=_("theater"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
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
        "localized_title",
        "localized_description",
        "orig_title",
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

    def to_indexable_titles(self) -> list[str]:
        titles = [t["text"] for t in self.localized_title if t["text"]]
        titles += [self.orig_title] if self.orig_title else []
        return list(set(titles))

    def to_indexable_doc(self):
        d = super().to_indexable_doc()
        d["people"] = (
            (self.orig_creator or [])
            + (self.playwright or [])
            + (self.director or [])
            + (self.troupe or [])
            + [a["name"] for a in (self.actor or [])]
            + (self.performer or [])
            + (self.composer or [])
            + (self.choreographer or [])
            + [a["name"] for a in (self.crew or [])]
        )
        d["company"] = self.troupe or []
        dt = self.opening_date or self.closing_date or ""
        dd = datetime_(dt)
        d["date"] = [int(dd.strftime("%Y%m%d"))] if dd else []
        d["genre"] = self.genre or []  # type:ignore
        return d

    def to_schema_org(self):
        """Generate Schema.org structured data for performance."""
        data: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "Play",
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

        if self.playwright:
            data["author"] = [
                {"@type": "Person", "name": person} for person in self.playwright
            ]

        if self.director:
            data["creator"] = [
                {"@type": "Person", "name": person} for person in self.orig_creator
            ]

        if self.composer:
            data["composer"] = [
                {"@type": "Person", "name": person} for person in self.composer
            ]

        if self.official_site:
            data["sameAs"] = self.official_site

        return data


class PerformanceProduction(Item):
    schema = PerformanceProductionSchema
    category = ItemCategory.Performance
    type = ItemType.PerformanceProduction
    url_path = "performance/production"
    show = models.ForeignKey(
        Performance, null=True, on_delete=models.SET_NULL, related_name="productions"
    )
    orig_title = jsondata.CharField(
        verbose_name=_("original title"), blank=True, default="", max_length=500
    )
    language = LanguageListField()
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
    orig_creator = jsondata.JSONField(
        verbose_name=_("original creator"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    composer = jsondata.JSONField(
        verbose_name=_("composer"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    choreographer = jsondata.JSONField(
        verbose_name=_("choreographer"),
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
        schema=_ACTOR_SCHEMA,
    )
    performer = jsondata.JSONField(
        verbose_name=_("performer"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    troupe = jsondata.JSONField(
        verbose_name=_("troupe"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
    )
    crew = jsondata.JSONField(
        verbose_name=_("crew"),
        null=False,
        blank=True,
        default=list,
        schema=_CREW_SCHEMA,
    )
    location = jsondata.JSONField(
        verbose_name=_("theater"),
        null=False,
        blank=True,
        default=list,
        schema=LIST_OF_STR_SCHEMA,
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
        "orig_title",
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
    def parent_item(self) -> Performance | None:
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
        return super().cover_image_url or (
            self.show.cover_image_url if self.show else None
        )

    def process_fetched_item(self, fetched, link_type):
        if (
            link_type == ExternalResource.LinkType.PARENT
            and isinstance(fetched, Performance)
            and self.show != fetched
        ):
            self.show = fetched
            return True
        return False

    @cached_property
    def crew_by_role(self):
        return _crew_by_role(self.crew)

    def to_indexable_titles(self) -> list[str]:
        titles = [t["text"] for t in self.localized_title if t["text"]]
        titles += [self.orig_title] if self.orig_title else []
        return list(set(titles))

    def to_indexable_doc(self):
        d = super().to_indexable_doc()
        d["people"] = (
            (self.orig_creator or [])
            + (self.playwright or [])
            + (self.director or [])
            + (self.troupe or [])
            + [a["name"] for a in (self.actor or [])]
            + (self.performer or [])
            + (self.composer or [])
            + (self.choreographer or [])
            + [a["name"] for a in (self.crew or [])]
        )
        d["company"] = self.troupe or []
        dt = self.opening_date or self.closing_date or ""
        dd = datetime_(dt)
        d["date"] = [int(dd.strftime("%Y%m%d"))] if dd else []
        return d

    def to_schema_org(self):
        """Generate Schema.org structured data for performance production."""
        data: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "TheaterEvent",
            "name": self.display_title,
            "url": self.absolute_url,
        }

        if self.orig_title and self.orig_title != self.display_title:
            data["alternateName"] = self.orig_title

        if self.display_description:
            data["description"] = self.display_description

        if self.has_cover():
            data["image"] = self.cover_image_url

        if self.opening_date:
            data["startDate"] = self.opening_date

        if self.closing_date:
            data["endDate"] = self.closing_date

        if self.location and len(self.location) > 0:
            data["location"] = {
                "@type": "PerformingArtsTheater",
                "name": self.location[0],
            }

        if self.language:
            data["inLanguage"] = self.language[0]  # type:ignore

        if self.troupe and len(self.troupe) > 0:
            data["performer"] = {"@type": "TheaterGroup", "name": self.troupe[0]}

        if self.director:
            data["director"] = [
                {"@type": "Person", "name": person} for person in self.director
            ]

        if self.actor:
            data["actor"] = [
                {
                    "@type": "Person",
                    "name": person["name"],
                }
                for person in self.actor
            ]

        if self.official_site:
            data["sameAs"] = self.official_site

        return data
