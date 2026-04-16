import json
from collections import OrderedDict
from functools import cached_property
from typing import TYPE_CHECKING, Any, Self

from django.db import models
from django.utils.translation import gettext_lazy as _
from loguru import logger
from ninja import Field, Schema

from common.models import get_current_locales, jsondata, uniq

if TYPE_CHECKING:
    from .item import ExternalResource

from .common import (
    LOCALIZED_DESCRIPTION_SCHEMA,
    LOCALIZED_LABEL_SCHEMA,
    IdType,
    LocalizedLabelSchema,
)
from .item import (
    Item,
    ItemCategory,
    ItemType,
    PrimaryLookupIdDescriptor,
)


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
    PLAYWRIGHT = "playwright", _("Playwright")
    DESIGNER = "designer", _("Designer")
    CHOREOGRAPHER = "choreographer", _("Choreographer")
    ORIGINAL_CREATOR = "original_creator", _("Original Creator")
    PRODUCER = "producer", _("Producer")

    # Organization roles
    PUBLISHER = "publisher", _("Publisher")
    DISTRIBUTOR = "distributor", _("Distributor")
    PRODUCTION_COMPANY = "production_company", _("Production Company")
    RECORD_LABEL = "record_label", _("Record Label")
    DEVELOPER = "developer", _("Developer")
    STUDIO = "studio", _("Studio")
    PUBLISHING_HOUSE = "publishing_house", _("Publishing House")
    IMPRINT = "imprint", _("Imprint")
    TROUPE = "troupe", _("Troupe")
    CREW = "crew", _("Crew")

    @classmethod
    def organization_roles(cls) -> set[str]:
        return {
            cls.PUBLISHER,
            cls.DISTRIBUTOR,
            cls.PRODUCTION_COMPANY,
            cls.RECORD_LABEL,
            cls.DEVELOPER,
            cls.STUDIO,
            cls.PUBLISHING_HOUSE,
            cls.IMPRINT,
            cls.TROUPE,
        }


class PeopleInSchema(Schema):
    name: str = Field(alias="display_name")
    bio: str = Field(default="", alias="display_description")
    people_type: str
    localized_name: list[LocalizedLabelSchema] = []
    localized_bio: list[LocalizedLabelSchema] = []
    cover_image_url: str | None
    birth_date: str | None = None
    death_date: str | None = None
    official_site: str | None = None


class PeopleSchema(Schema):
    id: str = Field(alias="absolute_url")
    uuid: str
    url: str
    api_url: str
    people_type: str
    display_name: str
    name: str = Field(alias="display_name")
    bio: str = Field(default="", alias="display_description")
    localized_name: list[LocalizedLabelSchema] = []
    localized_bio: list[LocalizedLabelSchema] = []
    cover_image_url: str | None
    birth_date: str | None = None
    death_date: str | None = None
    official_site: str | None = None
    imdb: str | None = None


class People(Item):
    """
    Model for people and organizations that can be linked to items with roles.
    Inherits from Item to share common functionality. Uses localized_name
    (not localized_title) for person/organization names.
    """

    schema = PeopleSchema
    category = ItemCategory.People
    url_path = "people"
    type = ItemType.People

    # People can have any role
    available_roles = list(PeopleRole)
    if TYPE_CHECKING:
        from .item import ItemCredit

        credited_items: models.QuerySet[ItemCredit]
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

    localized_bio = jsondata.JSONField(
        verbose_name=_("bio"),
        null=False,
        blank=True,
        default=list,
        schema=LOCALIZED_DESCRIPTION_SCHEMA,
    )

    # Metadata fields stored in JSONField via jsondata descriptors
    birth_date = jsondata.CharField(
        verbose_name=_("date of birth"), blank=True, default="", max_length=50
    )
    death_date = jsondata.CharField(
        verbose_name=_("date of death"), blank=True, default="", max_length=50
    )
    official_site = jsondata.URLField(
        verbose_name=_("website"), blank=True, default="", max_length=500
    )

    # External ID descriptors
    imdb = PrimaryLookupIdDescriptor(IdType.IMDB)
    tmdb_person = PrimaryLookupIdDescriptor(IdType.TMDB_Person)
    douban_personage = PrimaryLookupIdDescriptor(IdType.DoubanPersonage)

    METADATA_COPY_LIST = [
        "localized_name",
        "localized_bio",
        "birth_date",
        "death_date",
        "official_site",
    ]
    METADATA_MERGE_LIST = [
        "localized_name",
        "localized_bio",
    ]

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
        return None

    def get_localized_bio(self) -> str | None:
        if self.localized_bio:
            locales = get_current_locales()
            for loc in locales:
                v = next(
                    filter(lambda t: t["lang"] == loc, self.localized_bio), {}
                ).get("text")
                if v:
                    return v
        return None

    @cached_property
    def display_description(self) -> str:
        return self.get_localized_bio() or (
            self.localized_bio[0]["text"] if self.localized_bio else ""
        )

    @cached_property
    def display_name(self) -> str:
        return self.get_localized_name() or (
            self.localized_name[0]["text"] if self.localized_name else ""
        )

    @cached_property
    def display_title(self) -> str:
        return self.display_name

    @cached_property
    def additional_names(self) -> list[str]:
        name = self.display_name
        return uniq([t["text"] for t in self.localized_name if t["text"] != name])

    @cached_property
    def related_items_by_role(self) -> list[tuple[str, str, list]]:
        """Return related items grouped by role, as (role_value, role_label, items) tuples."""
        from .item import Item

        relations = self.item_relations.order_by("role").values_list("role", "item_id")
        role_item_ids: OrderedDict[str, list[int]] = OrderedDict()
        for role, item_id in relations:
            role_item_ids.setdefault(role, []).append(item_id)
        all_ids = [i for ids in role_item_ids.values() for i in ids]
        items_by_id = {
            item.pk: item
            for item in Item.objects.filter(
                pk__in=all_ids, is_deleted=False, merged_to_item__isnull=True
            )
        }
        return [
            (
                role,
                PeopleRole(role).label,
                [items_by_id[i] for i in ids if i in items_by_id],
            )
            for role, ids in role_item_ids.items()
            if any(i in items_by_id for i in ids)
        ]

    def link_matching_credits(self):
        """Find unlinked ItemCredits whose name matches this person and link them."""
        from .item import ItemCredit

        names = {n["text"] for n in (self.localized_name or []) if n.get("text")}
        if not names:
            return
        unlinked = ItemCredit.objects.filter(name__in=names, person__isnull=True)
        newly_linked_ids = list(unlinked.values_list("pk", flat=True))
        if not newly_linked_ids:
            return
        count = ItemCredit.objects.filter(pk__in=newly_linked_ids).update(person=self)
        logger.info(f"Linked {count} credits to {self.display_name}")
        # Create ItemPeopleRelation only for the newly linked credits
        for credit in (
            ItemCredit.objects.filter(pk__in=newly_linked_ids)
            .select_related("item")
            .all()
        ):
            role = self._credit_role_to_people_role(credit.role)
            if role:
                ItemPeopleRelation.objects.get_or_create(
                    item=credit.item, people=self, role=role
                )

    @staticmethod
    def _credit_role_to_people_role(credit_role: str) -> str | None:
        """Map CreditRole value to PeopleRole value."""
        mapping = {
            "author": PeopleRole.AUTHOR,
            "translator": PeopleRole.TRANSLATOR,
            "director": PeopleRole.DIRECTOR,
            "playwright": PeopleRole.PLAYWRIGHT,
            "actor": PeopleRole.ACTOR,
            "artist": PeopleRole.ARTIST,
            "designer": PeopleRole.DESIGNER,
            "composer": PeopleRole.COMPOSER,
            "choreographer": PeopleRole.CHOREOGRAPHER,
            "performer": PeopleRole.PERFORMER,
            "host": PeopleRole.HOST,
            "original_creator": PeopleRole.ORIGINAL_CREATOR,
            "crew": PeopleRole.CREW,
            "publisher": PeopleRole.PUBLISHER,
            "developer": PeopleRole.DEVELOPER,
            "production_company": PeopleRole.PRODUCTION_COMPANY,
            "record_label": PeopleRole.RECORD_LABEL,
            "distributor": PeopleRole.DISTRIBUTOR,
            "studio": PeopleRole.STUDIO,
            "troupe": PeopleRole.TROUPE,
        }
        return mapping.get(credit_role)

    @classmethod
    def create_from_external_resource(cls, p: "ExternalResource") -> Self:
        item = super().create_from_external_resource(p)
        # Set people_type from metadata if present (e.g., "organization" from Wikidata)
        people_type = p.metadata.get("people_type")
        if people_type and people_type in PeopleType.values:
            item.people_type = people_type
            item.save(update_fields=["people_type"])
        item.link_matching_credits()
        return item

    def is_deletable(self):
        return (
            not self.is_deleted
            and not self.merged_to_item_id
            and not self.merged_from_items.exists()
            and not self.item_relations.exists()
            and not self.credited_items.exists()
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

    def merge_credits(self, to_item):
        """Reparent ItemCredits from this person to the target person."""
        from .item import ItemCredit

        for credit in ItemCredit.objects.filter(person=self):
            credit.person = to_item
            credit.save(update_fields=["person"])

    def merge_to(self, to_item):
        super().merge_to(to_item)
        if not to_item:
            return
        self.merge_relations(to_item)
        self.merge_credits(to_item)

    @classmethod
    def find_by_name(
        cls, name: str, exact: bool = True, limit: int = 0
    ) -> list["People"]:
        """Find People by localized_name match.

        For partial match, uses indexed ItemCredit.name lookup first, then
        falls back to JSONB search for People without credits.
        """
        from .item import ItemCredit

        qs = cls.objects.filter(is_deleted=False, merged_to_item__isnull=True)
        if exact:
            results = qs.filter(metadata__localized_name__contains=[{"text": name}])
        else:
            # Find People linked from credits whose name matches (uses DB index)
            credit_people_ids = (
                ItemCredit.objects.filter(name__icontains=name, person__isnull=False)
                .values_list("person_id", flat=True)
                .distinct()
            )
            # Also search localized_name text values in JSONB
            json_qs = qs.extra(
                where=[
                    "EXISTS (SELECT 1 FROM jsonb_array_elements("
                    "metadata->'localized_name') elem "
                    "WHERE elem->>'text' ILIKE %s)"
                ],
                params=[f"%{name}%"],
            )
            results = qs.filter(pk__in=credit_people_ids) | json_qs
        results = results.distinct()
        if limit:
            results = results[:limit]
        return list(results)

    @classmethod
    def lookup_id_type_choices(cls):
        id_types = [
            IdType.WikiData,
            IdType.IMDB,
            IdType.TMDB_Person,
            IdType.DoubanPersonage,
            IdType.DoubanBook_Author,
            IdType.Goodreads_Author,
            IdType.Spotify_Artist,
            IdType.OpenLibrary_Author,
            IdType.IGDB_Company,
        ]
        return [(i.value, i.label) for i in id_types]

    @classmethod
    def lookup_id_cleanup(cls, lookup_id_type, lookup_id_value):
        if lookup_id_type == IdType.IMDB.value and lookup_id_value:
            v = lookup_id_value.strip()
            if not v.startswith("nm"):
                return None, None
            return lookup_id_type, v
        return super().lookup_id_cleanup(lookup_id_type, lookup_id_value)

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

        if self.birth_date:
            data["birthDate"] = self.birth_date

        if self.death_date:
            data["deathDate"] = self.death_date

        return data

    def to_schema_org_json(self):
        data = self.to_schema_org()
        return json.dumps(data, ensure_ascii=False, indent=2)

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
