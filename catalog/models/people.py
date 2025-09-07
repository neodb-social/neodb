import json
import uuid
from functools import cached_property
from typing import Any, Self

from django.core.signing import b62_decode
from django.db import models
from django.utils.translation import gettext_lazy as _
from loguru import logger
from ninja import Field, Schema

from common.models import get_current_locales, jsondata, uniq

from .common import LOCALIZED_LABEL_SCHEMA, LocalizedLabelSchema
from .item import Item, ItemCategory, ItemType


class PeopleType(models.TextChoices):
    PERSON = "person", _("Person")
    ORGANIZATION = "organization", _("Organization")


class PeopleRole(models.TextChoices):
    # Person roles
    AUTHOR = "author", _("Author")
    TRANSLATOR = "translator", _("Translator")
    PERFORMER = "performer", _("Performer")
    ACTOR = "actor", _("Actor")
    DIRECTOR = "director", _("Director")
    COMPOSER = "composer", _("Composer")
    ARTIST = "artist", _("Artist")
    VOICE_ACTOR = "voice_actor", _("Voice Actor")
    HOST = "host", _("Host")

    # Organization roles
    PUBLISHER = "publisher", _("Publisher")
    DISTRIBUTOR = "distributor", _("Distributor")
    PRODUCTION_COMPANY = "production_company", _("Production Company")
    RECORD_LABEL = "record_label", _("Record Label")
    DEVELOPER = "developer", _("Developer")
    STUDIO = "studio", _("Studio")


class PeopleInSchema(Schema):
    name: str = Field(alias="display_name")
    description: str = Field(default="", alias="display_description")
    people_type: str
    localized_name: list[LocalizedLabelSchema] = []
    localized_description: list[LocalizedLabelSchema] = []
    cover_image_url: str | None


class PeopleSchema(Schema):
    id: str = Field(alias="absolute_url")
    uuid: str
    url: str
    api_url: str
    people_type: str
    display_name: str
    name: str = Field(alias="display_name")
    description: str = Field(default="", alias="display_description")
    localized_name: list[LocalizedLabelSchema] = []
    localized_description: list[LocalizedLabelSchema] = []
    cover_image_url: str | None


class People(Item):
    """
    Model for people and organizations that can be linked to items with roles.
    Now inherits from Item to share common functionality.
    """

    schema = PeopleSchema
    category = ItemCategory.People
    url_path = "people"
    type = ItemType.People
    item_relations: models.QuerySet["ItemPeopleRelation"]
    people_type = models.CharField(
        _("type"), max_length=20, choices=PeopleType.choices, default=PeopleType.PERSON
    )
    localized_name = jsondata.JSONField(
        verbose_name=_("name"),
        null=False,
        blank=True,
        default=list,
        schema=LOCALIZED_LABEL_SCHEMA,
    )

    # localized_description is inherited from Item

    # # ManyToMany relationship with Items through ItemPeopleRelation
    # related_items = models.ManyToManyField(
    #     "catalog.Item",
    #     through="ItemPeopleRelation",
    #     related_name="related_people",
    #     blank=True,
    #     help_text=_("Items this person/organization is associated with"),
    # )

    # Metadata handling for merging
    METADATA_COPY_LIST = [
        "localized_name",
        "localized_description",
    ]
    METADATA_MERGE_LIST = [
        "localized_name",
        "localized_description",
    ]

    def __str__(self):
        return f"{self.__class__.__name__}|{self.pk}|{self.uuid} {self.primary_lookup_id_type}:{self.primary_lookup_id_value if self.primary_lookup_id_value else ''} ({self.display_name})"

    # uuid, url, absolute_url, and api_url properties are inherited from Item

    @property
    def is_person(self) -> bool:
        return self.people_type == PeopleType.PERSON

    @property
    def is_organization(self) -> bool:
        return self.people_type == PeopleType.ORGANIZATION

    def get_localized_name(self) -> str | None:
        if self.localized_name:
            locales = get_current_locales()
            for loc in locales:
                v = next(
                    filter(lambda t: t["lang"] == loc, self.localized_name), {}
                ).get("text")
                if v:
                    return v

    # get_localized_description is inherited from Item

    @cached_property
    def display_name(self) -> str:
        # return name in current locale if possible, otherwise any name
        return self.get_localized_name() or (
            self.localized_name[0]["text"] if self.localized_name else ""
        )

    @cached_property
    def additional_names(self) -> list[str]:
        name = self.display_name
        return uniq([t["text"] for t in self.localized_name if t["text"] != name])

    # display_description, brief_description, has_cover, cover_image_url, and default_cover_image_url are inherited from Item

    def is_deletable(self):
        return (
            not self.is_deleted
            and not self.merged_to_item_id
            and not self.merged_from_items.exists()
            and not self.item_relations.exists()  # has linked items
        )

    def merge_relations(self, to_item):
        for link in self.item_relations.all():
            existing_link = to_item.item_relations.filter(
                item=link.item, people=to_item, role=link.role
            ).first()
            if existing_link:
                if link.character and not existing_link.character:
                    existing_link.character = link.character
                    existing_link.save()
                link.delete()
            else:
                link.people = to_item
                link.save()

    def merge_to(self, to_item):
        super().merge_to(to_item)
        if not to_item:
            return
        self.merge_relations(to_item)

    @classmethod
    def get_by_url(cls, url_or_b62: str, resolve_merge=False) -> Self | None:
        import re

        b62 = url_or_b62.strip().split("/")[-1]
        if len(b62) not in [21, 22]:
            r = re.search(r"[A-Za-z0-9]{21,22}", url_or_b62)
            if r:
                b62 = r[0]
        try:
            people = cls.objects.get(uid=uuid.UUID(int=b62_decode(b62)))
            if resolve_merge:
                resolve_cnt = 5
                while people.merged_to_item and resolve_cnt > 0:
                    people = people.merged_to_item
                    resolve_cnt -= 1
                if resolve_cnt == 0:
                    logger.error(
                        "resolve merge loop error for people", extra={"people": people}
                    )
                    people = None
        except Exception:
            people = None
        return people

    def to_schema_org(self):
        data: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "Person" if self.is_person else "Organization",
            "name": self.display_name,
            "url": self.absolute_url,
        }

        if self.display_description:
            data["description"] = self.display_description

        if self.has_cover():
            data["image"] = self.cover_image_url

        return data

    def to_schema_org_json(self):
        data = self.to_schema_org()
        return json.dumps(data, ensure_ascii=False, indent=2)

    # ap_object is inherited from Item

    @property
    def ap_object_ref(self) -> dict[str, Any]:
        o = {
            "type": "Person" if self.is_person else "Organization",
            "href": self.absolute_url,
            "name": self.display_name,
        }
        if self.has_cover():
            o["image"] = self.cover_image_url or ""
        return o


class ItemPeopleRelation(models.Model):
    """Through model linking Items to People with roles"""

    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="people_relations"
    )
    people = models.ForeignKey(
        People, on_delete=models.CASCADE, related_name="item_relations"
    )
    role = models.CharField(_("role"), max_length=50, choices=PeopleRole.choices)
    character = jsondata.CharField(
        _("character"),
        max_length=1000,
        null=True,
        blank=True,
        help_text=_("Character name for actor roles"),
    )
    metadata = models.JSONField(_("metadata"), blank=True, null=True, default=dict)
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["item", "people", "role"]]
        indexes = [
            models.Index(fields=["item", "role"]),
        ]

    def __str__(self):
        return f"{self.pk}|{self.people_id}|{self.item_id}|{self.role}"  # type: ignore
