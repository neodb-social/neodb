import json
import re
import uuid
from enum import Enum
from functools import cached_property
from typing import TYPE_CHECKING, Any, Iterable, Self

from auditlog.context import disable_auditlog
from auditlog.models import LogEntry
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.indexes import GinIndex
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.signing import b62_decode, b62_encode
from django.db import connection, models
from django.db.models import Q, QuerySet, prefetch_related_objects
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from loguru import logger
from ninja import Field, Schema
from polymorphic.models import PolymorphicModel

from common.models import get_current_locales, jsondata, uniq
from common.models.genre import normalize_genres
from common.models.lang import normalize_languages
from common.utils import get_file_absolute_url

from .common import (
    LOCALIZED_DESCRIPTION_SCHEMA,
    LOCALIZED_LABEL_SCHEMA,
    IdealIdTypes,
    IdType,
    ItemCategory,
    ItemType,
    LocalizedLabelSchema,
    SiteName,
)
from .utils import item_cover_path, resource_cover_path

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise

    from journal.models import Collection, Mark
    from users.models import User

    from ..common import ResourceContent
    from .people import ItemPeopleRelation, PeopleRole


class PrimaryLookupIdDescriptor(object):  # TODO make it mixin of Field
    def __init__(self, id_type: IdType):
        self.id_type = id_type

    def __get__(
        self, instance: "Item | None", cls: type[Any] | None = None
    ) -> str | Self | None:
        if instance is None:
            return self
        if self.id_type != instance.primary_lookup_id_type:
            return None
        return instance.primary_lookup_id_value

    def __set__(self, instance: "Item", id_value: str | None):
        if id_value:
            instance.primary_lookup_id_type = self.id_type
            instance.primary_lookup_id_value = id_value
        else:
            instance.primary_lookup_id_type = None
            instance.primary_lookup_id_value = None


_PEOPLE_URL_RE = re.compile(
    r"(?:^|/)(?:people|person|organization)/([A-Za-z0-9]{21,22})(?:/|$)"
)


def _extract_people_uid(value: str) -> "uuid.UUID | None":
    """Return the UUID referenced by a /people|person|organization/<uuid>
    path (or a full URL containing one), or None if the value does not look
    like such a reference."""
    if not value:
        return None
    m = _PEOPLE_URL_RE.search(value)
    if not m:
        return None
    try:
        return uuid.UUID(int=b62_decode(m.group(1)))
    except Exception:
        return None


class LookupIdDescriptor(object):  # TODO make it mixin of Field
    def __init__(self, id_type: IdType):
        self.id_type = id_type

    def __get__(self, instance, cls=None):
        if instance is None:
            return self
        return instance.get_lookup_id(self.id_type)

    def __set__(self, instance, value):
        instance.set_lookup_id(self.id_type, value)


# class ItemId(models.Model):
#     item = models.ForeignKey('Item', models.CASCADE)
#     id_type = models.CharField(_("Id Type"), blank=False, choices=IdType.choices, max_length=50)
#     id_value = models.CharField(_("ID Value"), blank=False, max_length=1000)


class CreditRole(models.TextChoices):
    # Person roles
    Author = "author", _("author")
    Translator = "translator", _("translator")
    Director = "director", _("director")
    Playwright = "playwright", _("playwright")
    Actor = "actor", _("actor")
    Producer = "producer", _("producer")
    Artist = "artist", _("artist")
    Designer = "designer", _("designer")
    Composer = "composer", _("composer")
    Choreographer = "choreographer", _("choreographer")
    Performer = "performer", _("performer")
    Host = "host", _("host")
    OriginalCreator = "original_creator", _("original creator")
    Crew = "crew", _("crew")
    # Organization roles
    Publisher = "publisher", _("publisher")
    Developer = "developer", _("developer")
    ProductionCompany = "production_company", _("production company")
    RecordLabel = "record_label", _("record label")
    Distributor = "distributor", _("distributor")
    Studio = "studio", _("studio")
    Troupe = "troupe", _("troupe")


class ItemCredit(models.Model):
    """Links a person to an item with a role. Person FK is optional --
    when null, the credit is ad-hoc (name only, not linked to a People item)."""

    item = models.ForeignKey("Item", on_delete=models.CASCADE, related_name="credits")
    person = models.ForeignKey(
        "People",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="credited_items",
    )
    role = models.CharField(_("role"), max_length=100, choices=CreditRole.choices)
    name = models.CharField(_("name"), max_length=1000)
    character_name = models.CharField(
        _("character name"), blank=True, default="", max_length=500
    )
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["role", "order"]
        indexes = [
            models.Index(fields=["item", "role"]),
            models.Index(fields=["person"]),
            models.Index(fields=["name"]),
        ]

    @property
    def person_api_url(self) -> str | None:
        return self.person.api_url if self.person else None

    def __str__(self):
        linked = f" -> {self.person}" if self.person else ""
        return f"{self.name} ({self.role}) on {self.item}{linked}"


# def check_source_id(sid):
#     if not sid:
#         return True
#     s = sid.split(':')
#     if len(s) < 2:
#         return False
#     return sid[0] in IdType.values()


class ExternalResourceSchema(Schema):
    url: str


class CreditSchema(Schema):
    role: str
    name: str
    character_name: str = ""
    person_url: str | None = Field(None, alias="person_api_url")


class BaseSchema(Schema):
    id: str = Field(alias="absolute_url")
    type: str = Field(alias="ap_object_type")
    uuid: str
    url: str
    api_url: str
    category: ItemCategory
    parent_uuid: str | None
    display_title: str
    external_resources: list[ExternalResourceSchema] | None
    credits: list[CreditSchema] = Field([], alias="api_credits")


class ItemInSchema(Schema):
    type: str = Field(alias="get_type")
    title: str = Field(alias="display_title")
    description: str = Field(default="", alias="display_description")
    localized_title: list[LocalizedLabelSchema] = []
    localized_description: list[LocalizedLabelSchema] = []
    cover_image_url: str | None
    rating: float | None
    rating_count: int | None
    rating_distribution: list[int] | None
    tags: list[str] | None
    # brief is deprecated
    brief: str = Field(deprecated=True, alias="display_description")


class ItemSchema(BaseSchema, ItemInSchema):
    pass


class Item(PolymorphicModel):
    if TYPE_CHECKING:
        external_resources: QuerySet["ExternalResource"]
        collections: QuerySet["Collection"]
        merged_from_items: QuerySet["Item"]
        merged_to_item_id: int
        mark: "Mark"
        people_relations: QuerySet["ItemPeopleRelation"]
        credits: QuerySet["ItemCredit"]
    schema = ItemSchema
    category: ItemCategory  # subclass must specify this
    type: ItemType  # subclass must specify this
    url_path = "item"  # subclass must specify this
    child_class = None  # subclass may specify this to allow link to parent item
    parent_class = None  # subclass may specify this to allow create child item
    uid = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    title = models.CharField(_("title"), max_length=1000, default="")
    brief = models.TextField(_("description"), blank=True, default="")
    primary_lookup_id_type = models.CharField(
        _("Primary ID Type"), blank=False, null=True, max_length=50
    )
    primary_lookup_id_value = models.CharField(
        _("Primary ID Value"),
        blank=False,
        null=True,
        max_length=1000,
        help_text="automatically detected, usually no change necessary, left empty if unsure",
    )
    metadata = models.JSONField(_("metadata"), blank=True, null=True, default=dict)
    cover = models.ImageField(
        _("cover"),
        upload_to=item_cover_path,
        default=settings.DEFAULT_ITEM_COVER,
        blank=True,
    )
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)
    is_protected = models.BooleanField(null=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    merged_to_item = models.ForeignKey(
        "Item",
        null=True,
        on_delete=models.SET_NULL,
        default=None,
        related_name="merged_from_items",
    )

    localized_title = jsondata.JSONField(
        verbose_name=_("title"),
        null=False,
        blank=True,
        default=list,
        schema=LOCALIZED_LABEL_SCHEMA,
    )

    localized_description = jsondata.JSONField(
        verbose_name=_("description"),
        null=False,
        blank=True,
        default=list,
        schema=LOCALIZED_DESCRIPTION_SCHEMA,
    )

    class Meta:
        indexes = [
            models.Index(fields=["primary_lookup_id_type", "primary_lookup_id_value"])
        ]

    def is_deletable(self):
        return (
            not self.is_deleted
            and not self.merged_to_item_id
            and not self.merged_from_items.exists()
            and not self.child_items.exists()
        )

    def delete(
        self,
        using: Any = None,
        keep_parents: bool = False,
        soft: bool = True,
    ) -> tuple[int, dict[str, int]]:
        if soft:
            self.clear()
            self.is_deleted = True
            self.save(using=using)
            return 0, {}
        else:
            return super().delete(using=using, keep_parents=keep_parents)

    @cached_property
    def history(self):
        # can't use AuditlogHistoryField bc it will only return history with current content type
        return LogEntry.objects.filter(
            object_id=self.pk, content_type_id__in=list(item_content_types().values())
        )

    @cached_property
    def last_editor(self) -> "User | None":
        last_edit = self.history.order_by("-timestamp").first()
        return last_edit.actor if last_edit else None

    def clear(self):
        self.set_parent_item(None)
        self.primary_lookup_id_value = None
        self.primary_lookup_id_type = None
        for res in self.external_resources.all():
            res.item = None
            res.save()

    def __str__(self):
        return f"{self.__class__.__name__}|{self.pk}|{self.uuid} {self.primary_lookup_id_type}:{self.primary_lookup_id_value if self.primary_lookup_id_value else ''} ({self.display_title})"

    @classmethod
    def lookup_id_type_choices(cls):
        return IdType.choices

    @classmethod
    def lookup_id_cleanup(
        cls, lookup_id_type: str | IdType, lookup_id_value: str
    ) -> tuple[str | IdType, str] | tuple[None, None]:
        if not lookup_id_type or not lookup_id_value or not lookup_id_value.strip():
            return None, None
        return lookup_id_type, lookup_id_value.strip()

    @property
    def parent_item(self) -> Self | None:
        return None

    @property
    def child_items(self) -> "QuerySet[Item]":
        return Item.objects.none()

    @property
    def child_item_ids(self) -> list[int]:
        return list(self.child_items.values_list("id", flat=True))

    def set_parent_item(self, value: Self | None):
        # raise ValueError("cannot set parent item")
        pass

    @property
    def parent_uuid(self) -> str | None:
        return self.parent_item.uuid if self.parent_item else None

    @property
    def sibling_items(self) -> "QuerySet[Item]":
        return Item.objects.none()

    @property
    def title_deco(self) -> str:
        return ""

    @property
    def sibling_item_ids(self) -> list[int]:
        return list(self.sibling_items.values_list("id", flat=True))

    @classmethod
    def get_ap_object_type(cls) -> str:
        return cls.__name__

    @property
    def ap_object_type(self) -> str:
        return self.get_ap_object_type()

    @property
    def ap_object(self):
        return self.schema.from_orm(self).model_dump()

    @property
    def ap_object_ref(self) -> dict[str, Any]:
        o = {
            "type": self.get_ap_object_type(),
            "href": self.absolute_url,
            "name": self.display_title,
        }
        if self.has_cover():
            o["image"] = self.cover_image_url or ""
        return o

    def log_action(self, changes: dict[str, Any]):
        LogEntry.objects.log_create(
            self, action=LogEntry.Action.UPDATE, changes=changes
        )

    def merge_people_relations(self, to_item: Self) -> bool:
        """Merge people relations from this item to the target item"""
        updated = False
        for relation in self.people_relations.all():
            existing_relation = to_item.people_relations.filter(
                people=relation.people, role=relation.role
            ).first()
            if existing_relation:
                save_existing = False
                if relation.character and not existing_relation.character:
                    existing_relation.character = relation.character
                    save_existing = True
                if relation.metadata and not existing_relation.metadata:
                    existing_relation.metadata = relation.metadata
                    save_existing = True
                if save_existing:
                    existing_relation.save()
                relation.delete()
            else:
                relation.item = to_item
                relation.save()
                updated = True
        return updated

    def merge_to(self, to_item: Self | None):
        if to_item is None:
            if self.merged_to_item is not None:
                self.merged_to_item = None
                self.save()
            return
        if to_item.pk == self.pk:
            raise ValueError("cannot merge to self")
        if to_item.merged_to_item is not None:
            raise ValueError("cannot merge to item which is merged to another item")
        if to_item.is_deleted and not self.is_deleted:
            raise ValueError("cannot merge to item which is deleted")
        if not isinstance(to_item, self.__class__):
            raise ValueError("cannot merge to item in a different model")
        logger.debug(f"merging {self} to {to_item}")
        self.log_action({"!merged": [str(self.merged_to_item), str(to_item)]})
        self.merged_to_item = to_item
        self.save()
        for res in self.external_resources.all():
            res.item = to_item
            res.save()
        updated = False
        for k in to_item.METADATA_COPY_LIST:
            v = getattr(self, k)
            if v:
                if not getattr(to_item, k):
                    setattr(to_item, k, v)
                    updated = True
                elif k in self.METADATA_MERGE_LIST:
                    setattr(to_item, k, uniq(getattr(to_item, k, []) + (v or [])))
                    updated = True
        if self.has_cover() and not to_item.has_cover():
            to_item.cover = self.cover
            updated = True
        updated |= to_item.normalize_metadata()
        updated |= self.merge_people_relations(to_item)
        # Reparent ItemCredits to the target item
        for credit in self.credits.all():
            existing = to_item.credits.filter(
                role=credit.role, name=credit.name
            ).first()
            if existing:
                if credit.character_name and not existing.character_name:
                    existing.character_name = credit.character_name
                    existing.save(update_fields=["character_name"])
                if credit.person and not existing.person:
                    existing.person = credit.person
                    existing.save(update_fields=["person"])
                credit.delete()
            else:
                credit.item = to_item
                credit.save()
                updated = True
        to_item.log_action({"!merged_from": [str(self.merged_to_item), str(to_item)]})
        if updated:
            to_item.save()

    @property
    def final_item(self) -> Self:
        if self.merged_to_item:
            return self.merged_to_item.final_item
        return self

    def recast_to(self, model: "type[Item]") -> "Item":
        logger.warning(f"recast item {self} to {model}")
        if isinstance(self, model):
            return self
        if not issubclass(model, Item):
            raise ValueError("invalid model to recast to")
        ct = ContentType.objects.get_for_model(model)
        old_ct = self.polymorphic_ctype
        if not old_ct:
            raise ValueError("cannot recast item without polymorphic_ctype")
        tbl = self.__class__._meta.db_table
        with disable_auditlog():
            # disable audit as serialization won't work here
            obj = model(item_ptr_id=self.pk, polymorphic_ctype=ct)
            obj.save_base(raw=True)
            obj.save(update_fields=["polymorphic_ctype"])
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {tbl} WHERE item_ptr_id = %s", [self.pk])
        obj = model.objects.get(pk=obj.pk)
        obj.log_action({"!recast": [old_ct.model, ct.model]})
        return obj

    @property
    def uuid(self):
        return b62_encode(self.uid.int).zfill(22)

    @property
    def url(self):
        return f"/{self.url_path}/{self.uuid}"

    @property
    def absolute_url(self):
        return f"{settings.SITE_INFO['site_url']}{self.url}"

    @property
    def api_url(self):
        return f"/api{self.url}"

    def get_type(self) -> str:
        return self.__class__.__name__

    @property
    def class_name(self) -> str:
        return self.__class__.__name__.lower()

    def get_localized_title(self) -> str | None:
        if self.localized_title:
            locales = get_current_locales()
            for loc in locales:
                v = next(
                    filter(lambda t: t["lang"] == loc, self.localized_title), {}
                ).get("text")
                if v:
                    return v

    def get_localized_description(self) -> str | None:
        if self.localized_description:
            locales = get_current_locales()
            for loc in locales:
                v = next(
                    filter(lambda t: t["lang"] == loc, self.localized_description), {}
                ).get("text")
                if v:
                    return v

    @cached_property
    def display_resources(self) -> "list[ExternalResource]":
        resources = list(self.external_resources.all())
        types = {res.id_type for res in resources}
        if IdType.WikiData in types or not self.parent_item:
            return resources
        wd = self.parent_item.external_resources.filter(id_type=IdType.WikiData).first()
        if wd:
            resources.append(wd)
        return resources

    @cached_property
    def display_title(self) -> str:
        # return title in current locale if possible, otherwise any title
        return (self.get_localized_title() or self.title) or (
            self.localized_title[0]["text"] if self.localized_title else ""
        )

    @cached_property
    def additional_title(self) -> list[str]:
        title = self.display_title
        return uniq([t["text"] for t in self.localized_title if t["text"] != title])

    @cached_property
    def display_description(self) -> str:
        return (
            self.get_localized_description()
            or self.brief
            or (
                self.localized_description[0]["text"]
                if self.localized_description
                else ""
            )
        )

    @property
    def brief_description(self):
        return (str(self.display_description) or "")[:155]

    def to_indexable_titles(self) -> list[str]:
        titles = [t["text"] for t in self.localized_title if t["text"]]
        if self.parent_item:
            titles += self.parent_item.to_indexable_titles()
        return list(set(titles))

    def to_indexable_doc(self) -> dict[str, str | int | list[str] | list[int]]:
        from .people import PeopleRole

        org_roles = PeopleRole.organization_roles()
        people: list[str] = []
        company: list[str] = []
        for role, credits in self.role_credits.items():
            names = [c.name for c in credits]
            if role in org_roles:
                company.extend(names)
            else:
                people.extend(names)
        doc = {
            "id": str(self.pk),
            "item_id": [self.pk],
            "item_class": self.__class__.__name__,
            "title": self.to_indexable_titles(),
            "tag": self.tags,
            "mark_count": self.mark_count,
            "language": getattr(self, "language", None) or [],
            "people": people,
            "company": company,
        }
        return doc

    def update_index(self, later: bool = False):
        from catalog.search import CatalogIndex

        if later:
            CatalogIndex.enqueue_replace_items([self.pk])
        else:
            index = CatalogIndex.instance()
            index.replace_item(self)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.update_index()

    @classmethod
    def get_by_url(cls, url_or_b62: str, resolve_merge=False) -> Self | None:
        b62 = url_or_b62.strip().split("/")[-1]
        if len(b62) not in [21, 22]:
            r = re.search(r"[A-Za-z0-9]{21,22}", url_or_b62)
            if r:
                b62 = r[0]
        try:
            item = cls.objects.get(uid=uuid.UUID(int=b62_decode(b62)))
            if resolve_merge:
                resolve_cnt = 5
                while item.merged_to_item and resolve_cnt > 0:
                    item = item.merged_to_item
                    resolve_cnt -= 1
                if resolve_cnt == 0:
                    logger.error(
                        "resolve merge loop error for item", extra={"item": item}
                    )
                    item = None
        except Exception:
            item = None
        return item

    @classmethod
    def get_by_remote_url(cls, url: str) -> "Item | None":
        url_ = url.replace("/~neodb~/", "/")
        if url_.startswith(settings.SITE_INFO["site_url"]):
            return cls.get_by_url(url_, True)
        r = ExternalResource.objects.filter(url=url_).first()
        return r.item if r else None

    @classmethod
    def get_by_ids(cls, ids: list[int]):
        select = {f"id_{i}": f"id={i}" for i in ids}
        order = [f"-id_{i}" for i in ids]
        items = (
            cls.objects.filter(pk__in=ids, is_deleted=False)
            .prefetch_related("external_resources")
            .extra(select=select, order_by=order)
        )
        return items

    @classmethod
    def get_final_items(cls, items: Iterable["Item"]) -> list["Item"]:
        return [j for j in [i.final_item for i in items] if not j.is_deleted]

    @staticmethod
    def prefetch_parent_items(items: "Iterable[Item]") -> None:
        """Batch-prefetch parent item relationships to avoid N+1 queries.

        Call this on a list of polymorphic Item instances before rendering
        templates that access item.parent_item (e.g. _item_card_metadata_base).
        Note: Edition is excluded because the template short-circuits
        parent_item access for editions (type.value == 'edition'). Callers
        that serialize through ItemSchema (which reads parent_uuid
        unconditionally) should additionally call ``Item.prefetch_edition_works``.
        """
        from .performance import PerformanceProduction
        from .podcast import PodcastEpisode
        from .tv import TVEpisode, TVSeason

        tvseasons: list[TVSeason] = []
        tvepisodes: list[TVEpisode] = []
        podcastepisodes: list[PodcastEpisode] = []
        performanceproductions: list[PerformanceProduction] = []

        for i in items:
            if isinstance(i, TVSeason):
                tvseasons.append(i)
            elif isinstance(i, TVEpisode):
                tvepisodes.append(i)
            elif isinstance(i, PodcastEpisode):
                podcastepisodes.append(i)
            elif isinstance(i, PerformanceProduction):
                performanceproductions.append(i)

        if tvseasons:
            prefetch_related_objects(tvseasons, "show")
        if tvepisodes:
            prefetch_related_objects(tvepisodes, "season")
        if podcastepisodes:
            prefetch_related_objects(podcastepisodes, "program")
        if performanceproductions:
            prefetch_related_objects(performanceproductions, "show")

    @staticmethod
    def prefetch_edition_works(items: "Iterable[Item]") -> None:
        """Batch-prefetch Edition.works for ItemSchema/parent_uuid serialization.

        Edition.parent_item calls Edition.get_work() -> self.works.all()[0],
        which would fire one ORDER-BY-pk-LIMIT-1 query per edition when the
        schema reads parent_uuid. Templates that short-circuit parent_item for
        editions don't need this.
        """
        from .book import Edition

        editions = [i for i in items if isinstance(i, Edition)]
        if editions:
            prefetch_related_objects(editions, "works")

    @staticmethod
    def descendant_ids_with_ancestor_in(item_ids: "Iterable[int]") -> set[int]:
        """IDs within item_ids whose parent (or grandparent, for TVEpisode)
        is also in item_ids.

        Used to hide redundant child entries when both the parent and child
        appear in a list (e.g. Work + its Edition, TVShow + its TVSeason).
        Runs a bounded set of indexed id-only lookups; no item rows are
        fetched.
        """
        from .book import Work
        from .performance import PerformanceProduction
        from .podcast import PodcastEpisode
        from .tv import TVEpisode, TVSeason

        ids = list(item_ids)
        hidden: set[int] = set()
        if not ids:
            return hidden

        # Edition -> Work via Work.editions M2M
        hidden.update(
            Work.editions.through.objects.filter(
                edition_id__in=ids, work_id__in=ids
            ).values_list("edition_id", flat=True)
        )
        # TVSeason -> TVShow
        hidden.update(
            TVSeason.objects.filter(pk__in=ids, show_id__in=ids).values_list(
                "pk", flat=True
            )
        )
        # TVEpisode -> TVSeason or TVShow (grandparent)
        hidden.update(
            TVEpisode.objects.filter(pk__in=ids)
            .filter(Q(season_id__in=ids) | Q(season__show_id__in=ids))
            .values_list("pk", flat=True)
        )
        # PodcastEpisode -> Podcast
        hidden.update(
            PodcastEpisode.objects.filter(pk__in=ids, program_id__in=ids).values_list(
                "pk", flat=True
            )
        )
        # PerformanceProduction -> Performance
        hidden.update(
            PerformanceProduction.objects.filter(
                pk__in=ids, show_id__in=ids
            ).values_list("pk", flat=True)
        )
        return hidden

    METADATA_COPY_LIST = [
        # "title",
        # "brief",
        "localized_title",
        "localized_description",
    ]  # list of metadata keys to copy from resource to item
    METADATA_MERGE_LIST = [
        "localized_title",
        "localized_description",
    ]

    @classmethod
    def copy_metadata(cls, metadata: dict[str, Any]) -> dict[str, Any]:
        d = {
            k: v
            for k, v in metadata.items()
            if k in cls.METADATA_COPY_LIST and v is not None
        }
        d = {k: uniq(v) if k in cls.METADATA_MERGE_LIST else v for k, v in d.items()}
        return d

    def has_cover(self) -> bool:
        return bool(self.cover) and self.cover != settings.DEFAULT_ITEM_COVER

    @property
    def cover_image_url(self) -> str | None:
        return get_file_absolute_url(self.cover)

    @property
    def default_cover_image_url(self) -> str:
        return f"{settings.SITE_INFO['site_url']}{settings.DEFAULT_ITEM_COVER}"

    # Mapping from jsondata field name to CreditRole value for auto-sync
    CREDIT_FIELD_MAPPING: dict[str, str] = {}

    @classmethod
    def credit_role_choices(cls):
        """CreditRole choices relevant to this item class.

        Derived from the class's `available_roles` (PeopleRole values share
        string values with CreditRole) plus any role in CREDIT_FIELD_MAPPING.
        Falls back to all CreditRole choices if neither is set.
        """
        allowed: set[str] = {str(r) for r in getattr(cls, "available_roles", [])}
        allowed.update(cls.CREDIT_FIELD_MAPPING.values())
        allowed &= set(CreditRole.values)
        if not allowed:
            return list(CreditRole.choices)
        return [(v, label) for v, label in CreditRole.choices if v in allowed]

    @classmethod
    def create_from_external_resource(cls, p: "ExternalResource") -> Self:
        logger.debug(f"creating new item from {p}")
        obj = cls.copy_metadata(p.metadata)
        item = cls(**obj)
        if p.has_cover():
            item.cover = p.cover
        item.normalize_metadata([p])
        item.save()
        item.ap_object  # validate schema
        item.sync_credits_from_metadata()
        return item

    def _update_primary_lookup_id(self, override_resources=[]) -> bool:
        """
        Update primary_lookup_id from external resources
        """
        lookup_ids = {}
        r = None
        resources = override_resources or self.external_resources.all()
        for res in resources:
            r = res
            lookup_ids.update(res.other_lookup_ids or {})
            lookup_ids[res.id_type] = res.id_value
        if not lookup_ids:
            logger.warning(f"Item {self}: no available lookup_ids")
            return False
        pid = (None, None)
        for t in IdealIdTypes:
            if t in lookup_ids and lookup_ids[t]:
                pid = (t, lookup_ids[t])
                break
        if r and pid == (None, None):
            pid = (r.id_type, r.id_value)
        if (
            self.primary_lookup_id_type,
            self.primary_lookup_id_value,
        ) != pid and pid != (None, None):
            self.primary_lookup_id_type = pid[0]
            self.primary_lookup_id_value = pid[1]
            logger.debug(f"Updated primary_lookup_id for {self} to {pid}")
            return True
        return False

    def _normalize_languages(self):
        changed = False
        # for field_name in ("localized_title", "localized_description"):
        #     original_list = getattr(self, field_name)
        #     new_list = [
        #         {normalize_language(x["lang"]) or "x": x["text"]}
        #         for x in original_list
        #         if "lang" in x and "text" in x
        #     ]
        #     if original_list != new_list:
        #         setattr(self, field_name, new_list)
        #         changed = True
        if hasattr(self, "language"):  # normalize language list
            language = normalize_languages(self.language)
            if self.language != language:
                self.language = language
                changed = True
        return changed

    def _normalize_genres(self) -> bool:
        changed = False
        if hasattr(self, "genre"):
            genre = normalize_genres(self.genre)
            if self.genre != genre:
                self.genre = genre
                changed = True
        return changed

    def normalize_metadata(self, override_resources=[]) -> bool:
        r = self._update_primary_lookup_id(override_resources)
        r |= self._normalize_languages()
        r |= self._normalize_genres()
        return r

    def merge_data_from_external_resource(
        self, p: "ExternalResource", ignore_existing_content: bool = False
    ):
        logger.debug(f"merging data from {p} to {self}")
        for k in self.METADATA_COPY_LIST:
            v = p.metadata.get(k)
            if v:
                if not getattr(self, k) or ignore_existing_content:
                    setattr(self, k, v)
                elif k in self.METADATA_MERGE_LIST:
                    setattr(self, k, uniq(getattr(self, k, []) + (v or [])))
        if p.cover and (not self.has_cover() or ignore_existing_content):
            self.cover = p.cover
        self.normalize_metadata()
        self.save()
        self.ap_object  # validate schema
        self.sync_credits_from_metadata()

    def sync_credits_from_metadata(self, prune: bool = True):
        """Sync ItemCredit rows from jsondata credit fields.

        For each role mapped in CREDIT_FIELD_MAPPING, rebuild ItemCredit rows
        from the jsondata list: create/update matching rows, link to a People
        when the raw value is a /people|person|organization/<uuid> reference
        or when an existing ItemCredit for the same name is already linked.
        When a People is resolved, the jsondata entry is rewritten to the
        canonical ``person.url`` form so subsequent edits show a link. When
        ``prune`` is true, ItemCredit rows no longer present are deleted.
        """
        from .people import People

        if not self.CREDIT_FIELD_MAPPING:
            return

        managed_roles = set(self.CREDIT_FIELD_MAPPING.values())
        credits_by_role: dict[str, list[ItemCredit]] = {r: [] for r in managed_roles}
        for c in self.credits.select_related("person").filter(role__in=managed_roles):
            credits_by_role.setdefault(c.role, []).append(c)

        # Collect every People UUID referenced across all managed fields so we
        # can resolve them in one query instead of per-value.
        uuids: set[uuid.UUID] = set()

        def _iter_raw_names():
            for field_name in self.CREDIT_FIELD_MAPPING:
                values = getattr(self, field_name, None) or []
                if isinstance(values, str):
                    values = [values]
                for v in values:
                    if isinstance(v, dict):
                        yield (v.get("name") or "").strip()
                    else:
                        yield str(v or "").strip()

        for raw in _iter_raw_names():
            uid = _extract_people_uid(raw)
            if uid is not None:
                uuids.add(uid)
        people_by_uid = (
            {p.uid: p for p in People.objects.filter(uid__in=uuids)} if uuids else {}
        )

        def _resolve(raw: str) -> tuple[People | None, str]:
            uid = _extract_people_uid(raw)
            if uid is None:
                return None, raw
            person = people_by_uid.get(uid)
            if not person:
                return None, raw
            return person, person.display_name or raw

        metadata_changed = False
        for field_name, credit_role in self.CREDIT_FIELD_MAPPING.items():
            values = getattr(self, field_name, None) or []
            if isinstance(values, str):
                values = [values]

            existing = credits_by_role.get(credit_role, [])
            linked_by_name = {c.name: c.person for c in existing if c.person}

            desired: list[tuple[str, str, People | None]] = []
            new_values: list[str | dict] = []
            for value in values:
                if isinstance(value, dict):
                    raw_name = (value.get("name") or "").strip()
                    character = (value.get("role") or "").strip()
                else:
                    raw_name = str(value or "").strip()
                    character = ""
                if not raw_name:
                    new_values.append(value)
                    continue
                person, display = _resolve(raw_name)
                if person is None and raw_name in linked_by_name:
                    person = linked_by_name[raw_name]
                    display = person.display_name or raw_name
                canonical = person.url if person else raw_name
                if canonical != raw_name:
                    metadata_changed = True
                if isinstance(value, dict):
                    new_values.append({**value, "name": canonical})
                else:
                    new_values.append(canonical)
                desired.append((display, character, person))

            if new_values != values:
                setattr(self, field_name, new_values)

            existing_by_key: dict[tuple[str, int | None], ItemCredit] = {}
            for c in existing:
                key = (c.name, c.person.pk if c.person else None)
                existing_by_key.setdefault(key, c)

            used_pks: set[int] = set()
            for order, (name, character, person) in enumerate(desired):
                key = (name, person.pk if person else None)
                credit = existing_by_key.get(key)
                if credit is not None and credit.pk not in used_pks:
                    used_pks.add(credit.pk)
                    update_fields = []
                    if credit.order != order:
                        credit.order = order
                        update_fields.append("order")
                    if (credit.character_name or "") != character:
                        credit.character_name = character
                        update_fields.append("character_name")
                    if update_fields:
                        credit.save(update_fields=update_fields)
                else:
                    ItemCredit.objects.create(
                        item=self,
                        role=credit_role,
                        name=name,
                        character_name=character,
                        person=person,
                        order=order,
                    )

            if prune:
                stale = [c.pk for c in existing if c.pk not in used_pks]
                if stale:
                    ItemCredit.objects.filter(pk__in=stale).delete()

        if metadata_changed:
            self.save(update_fields=["metadata"])
        # Invalidate cached credits so subsequent reads reflect the new data
        self.__dict__.pop("role_credits", None)

    def process_fetched_item(
        self, fetched: Self, link_type: "ExternalResource.LinkType"
    ) -> bool:
        """Subclass may override this"""
        return False

    def is_editable(self):
        return not self.is_deleted and self.merged_to_item is None

    def get_people_by_role(self, role: "PeopleRole"):
        from .people import People

        return People.objects.filter(
            item_relations__item=self, item_relations__role=role
        )

    def get_credits_by_role(self, role: str) -> "QuerySet[ItemCredit]":
        return self.credits.filter(role=role).select_related("person")

    def _credits_with_person(self) -> list["ItemCredit"]:
        # Use the prefetch cache when callers have prefetched
        # `credits` (ideally with `credits__person`). Applying
        # `.select_related("person")` here would bypass that cache
        # and re-query once per item.
        if "credits" in getattr(self, "_prefetched_objects_cache", {}):
            return list(self.credits.all())
        return list(self.credits.select_related("person").all())

    @cached_property
    def api_credits(self) -> list["ItemCredit"]:
        """Credits for API serialization."""
        return self._credits_with_person()

    def credit_names_by_role(self, role: str) -> list[str]:
        """Return credit names as list[str] from the credits table."""
        return [c.name for c in self.role_credits.get(role, [])]

    @cached_property
    def role_credits(self) -> dict[str, list["ItemCredit"]]:
        """All credits grouped by role, for template access."""
        result: dict[str, list[ItemCredit]] = {}
        for credit in self._credits_with_person():
            result.setdefault(credit.role, []).append(credit)
        return result

    @property
    def mark_count(self):
        from journal.models import Mark

        return Mark.get_mark_count_for_item(self)

    @cached_property
    def rating_info(self):
        from journal.models import Rating

        return Rating.get_info_for_item(self)

    @property
    def rating(self):
        return self.rating_info.get("average")

    @cached_property
    def rating_count(self):
        return self.rating_info.get("count")

    @cached_property
    def rating_distribution(self):
        return self.rating_info.get("distribution")

    @cached_property
    def tags(self):
        from journal.models import TagManager

        return TagManager.indexable_tags_for_item(self)

    def journal_exists(self):
        from journal.models import journal_exists_for_item

        return journal_exists_for_item(self)

    def to_schema_org(self):
        data: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "Thing",
            "name": self.display_title,
            "url": self.absolute_url,
        }

        if self.display_description:
            data["description"] = self.display_description

        if self.has_cover():
            data["image"] = self.cover_image_url

        if self.rating:
            data["aggregateRating"] = {
                "@type": "AggregateRating",
                "ratingValue": self.rating,
                "reviewCount": self.rating_count,
                "worstRating": 1,
                "bestRating": 10,
            }

        return data

    def to_schema_org_json(self):
        data = self.to_schema_org()
        return json.dumps(data, ensure_ascii=False, indent=2)


class ItemLookupId(models.Model):
    item = models.ForeignKey(
        Item, null=True, on_delete=models.SET_NULL, related_name="lookup_ids"
    )
    id_type = models.CharField(
        _("source site"), blank=True, choices=IdType.choices, max_length=50
    )
    id_value = models.CharField(_("ID on source site"), blank=True, max_length=1000)
    raw_url = models.CharField(
        _("source url"), blank=True, max_length=1000, unique=True
    )

    class Meta:
        unique_together = [["id_type", "id_value"]]


class ExternalResource(models.Model):
    if TYPE_CHECKING:
        required_resources: list[dict[str, str]]
        related_resources: list[dict[str, str]]
        prematched_resources: list[dict[str, str]]
    item = models.ForeignKey(
        Item, null=True, on_delete=models.SET_NULL, related_name="external_resources"
    )
    id_type = models.CharField(
        _("IdType of the source site"),
        blank=False,
        choices=IdType.choices,
        max_length=50,
    )
    id_value = models.CharField(
        _("Primary Id on the source site"), blank=False, max_length=1000
    )
    url = models.CharField(
        _("url to the resource"), blank=False, max_length=1000, unique=True
    )
    cover = models.ImageField(
        upload_to=resource_cover_path, default=settings.DEFAULT_ITEM_COVER, blank=True
    )
    other_lookup_ids = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)
    scraped_time = models.DateTimeField(null=True)
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    class LinkType(Enum):
        PREMATCHED = "prematch"
        CHILD = "child"
        PARENT = "parent"

    required_resources = jsondata.ArrayField(
        models.CharField(), null=False, blank=False, default=list
    )
    """ links required to generate Item from this resource, e.g. parent TVShow of TVSeason """

    related_resources = jsondata.ArrayField(
        models.CharField(), null=False, blank=False, default=list
    )
    """links related to this resource which may be fetched later, e.g. sub TVSeason of TVShow"""

    prematched_resources = jsondata.ArrayField(
        models.CharField(), null=False, blank=False, default=list
    )
    """links to help match an existing Item from this resource, *to be deprecated* """

    class Meta:
        unique_together = [["id_type", "id_value"]]
        indexes = [
            GinIndex(fields=["other_lookup_ids"], name="catalog_extres_lookup_ids_gin")
        ]

    def __str__(self):
        return f"{self.pk}:{self.id_type}:{self.id_value or ''} ({self.url})"

    def has_cover(self) -> bool:
        return bool(self.cover) and self.cover != settings.DEFAULT_ITEM_COVER

    def _match_existing_item(self, model: type[Item]) -> Item | None:
        """
        try match an existing Item in the following order:
        - id_type/id_value matches item's primary type/value (should be deprecated)
        - any other_lookup_ids matches item's primary type/value (should be deprecated)
        - id_type/id_value matches item's resources's any other_lookup_ids
        - any other_lookup_ids matches item's resources's type/value
        - any other_lookup_ids matches item's resources's any other_lookup_ids
        """
        ct = item_content_types().get(model)
        if not ct:
            logger.error(f"Unknown item model {model}")
            return None
        items = model.objects.filter(is_deleted=False, merged_to_item__isnull=True)
        resources = ExternalResource.objects.filter(
            item_id__isnull=False, item__polymorphic_ctype_id=ct
        )

        item = items.filter(
            primary_lookup_id_type=self.id_type, primary_lookup_id_value=self.id_value
        ).first()
        if item:
            return item

        if self.other_lookup_ids:
            query = Q()
            for t, v in self.other_lookup_ids.items():
                if not v:
                    continue
                query |= Q(primary_lookup_id_type=t, primary_lookup_id_value=v)
            if query != Q():
                item = items.filter(query).first()
                if item:
                    return item

        res = resources.filter(
            other_lookup_ids__contains={self.id_type: self.id_value}
        ).first()
        if res:
            return res.item

        if self.other_lookup_ids:
            query = Q()
            for t, v in self.other_lookup_ids.items():
                if not v:
                    continue
                query |= Q(id_type=t, id_value=v)
            if query != Q():
                res = resources.filter(query).first()
                if res:
                    return res.item

            query = Q()
            for t, v in self.other_lookup_ids.items():
                if not v:
                    continue
                query |= Q(other_lookup_ids__contains={t: v})
            if query != Q():
                res = resources.filter(query).first()
                if res:
                    return res.item

    def match_and_link_item(
        self, default_model: type[Item] | None, ignore_existing_content: bool
    ) -> bool:
        """find an existing Item for this resource or create a new one, then link to it"""
        created = False
        try:
            previous_item: Item | None = self.item
        except Item.DoesNotExist:
            logger.error(f"PolymorphicModel Item {self.item_id} error fk from {self}")  # type: ignore
            previous_item = None
        model = self.get_item_model(default_model)
        self.item = self._match_existing_item(model=model) or previous_item
        if self.item is None:  # matching to a new item
            self.item = model.create_from_external_resource(self)
            self.save(update_fields=["item"])
            created = True
        elif previous_item != self.item:  # matching to another item
            self.save(update_fields=["item"])
            self.item.merge_data_from_external_resource(self, ignore_existing_content)
        elif ignore_existing_content:  # matching to same item but overwriting requested
            self.item.merge_data_from_external_resource(self, ignore_existing_content)
        if previous_item != self.item:
            if previous_item:
                previous_item.log_action({"!unmatch": [str(self), ""]})
            self.item.log_action({"!match": ["", str(self)]})
        return created

    def unlink_from_item(self):
        if not self.item:
            return
        self.item.log_action({"!unlink": [str(self), None]})
        self.item = None
        self.save()

    def get_site(self):
        from ..common import SiteManager

        return SiteManager.get_site_cls_by_id_type(self.id_type)

    @property
    def site_name(self) -> SiteName:
        try:
            site = self.get_site()
            return site.SITE_NAME if site else SiteName.Unknown
        except Exception:
            logger.warning(f"Unknown site for {self}")
            return SiteName.Unknown

    @property
    def site_label(self) -> "StrOrPromise":
        if self.id_type == IdType.Fediverse:
            from takahe.utils import Takahe

            domain = self.id_value.split("://")[1].split("/")[0]
            n = Takahe.get_node_name_for_domain(domain)
            return n or domain
        return self.site_name.label

    def update_content(self, resource_content: "ResourceContent"):
        self.other_lookup_ids = resource_content.lookup_ids
        self.metadata = resource_content.metadata
        if (
            resource_content.metadata.get("cover_image_url")
            and not resource_content.cover_image
        ):
            from ..common import BasicImageDownloader

            (
                resource_content.cover_image,
                resource_content.cover_image_extention,
            ) = BasicImageDownloader.download_image(
                resource_content.metadata.get("cover_image_url"), self.url
            )
        if resource_content.cover_image and resource_content.cover_image_extention:
            self.cover = SimpleUploadedFile(
                "temp." + resource_content.cover_image_extention,
                resource_content.cover_image,
            )
        elif resource_content.metadata.get("cover_image_path"):
            self.cover = resource_content.metadata.get("cover_image_path")
        self.scraped_time = timezone.now()
        self.save()

    @property
    def ready(self):
        return bool(self.metadata and self.scraped_time)

    def get_all_lookup_ids(self) -> dict[str, str]:
        d = self.other_lookup_ids.copy()
        d[self.id_type] = self.id_value
        d = {k: v for k, v in d.items() if bool(v)}
        return d

    def get_item_model(self, default_model: type[Item] | None) -> type[Item]:
        model = self.metadata.get("preferred_model")
        if model:
            m = ContentType.objects.filter(
                app_label="catalog", model=model.lower()
            ).first()
            if m:
                mc: type[Item] | None = m.model_class()  # type: ignore
                if not mc:
                    raise ValueError(
                        f"preferred model {model} does not exist in ContentType"
                    )
                return mc
            else:
                raise ValueError(f"preferred model {model} does not exist")
        if not default_model:
            raise ValueError("no default preferred model specified")
        return default_model

    def process_fetched_resource(self, fetched: Self, link_type: LinkType):
        if self.item and fetched.item:
            return self.item.process_fetched_item(fetched.item, link_type)
        else:
            return False


_CONTENT_TYPE_LIST = None


def item_content_types() -> dict[type[Item], int]:
    global _CONTENT_TYPE_LIST
    if _CONTENT_TYPE_LIST is None:
        _CONTENT_TYPE_LIST = {}
        for cls in Item.__subclasses__():
            _CONTENT_TYPE_LIST[cls] = ContentType.objects.get(
                app_label="catalog", model=cls.__name__.lower()
            ).id
    return _CONTENT_TYPE_LIST


_CATEGORY_LIST = None


def item_categories() -> dict[ItemCategory, list[type[Item]]]:
    global _CATEGORY_LIST
    if _CATEGORY_LIST is None:
        _CATEGORY_LIST = {}
        for cls in Item.__subclasses__():
            c = getattr(cls, "category", None)
            if c not in _CATEGORY_LIST:
                _CATEGORY_LIST[c] = [cls]
            else:
                _CATEGORY_LIST[c].append(cls)
    return _CATEGORY_LIST
