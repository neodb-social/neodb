from functools import cached_property
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import django.dispatch
import django_rq
from django.db import models, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from loguru import logger

from catalog.models import Item, ItemCategory
from catalog.models.item import item_content_types
from common.validators import is_valid_url
from takahe.utils import Takahe
from users.models import APIdentity

from .common import Piece, VisibilityType

list_add = django.dispatch.Signal()
list_remove = django.dispatch.Signal()

# Per-page item limit for the AP `OrderedCollectionPage` items endpoint.
# Bounds the work a single inbound page-fetch can drive (DB I/O + queued
# catalog fetches), matched on the outbound paginator side. There is no
# upper bound on the *total* number of pages a list may span.
AP_PAGE_SIZE = 100


class List(Piece):
    """
    List (abstract model)
    """

    if TYPE_CHECKING:
        MEMBER_CLASS: "type[ListMember]"
        members: "models.QuerySet[ListMember]"
        items: "models.ManyToManyField[Item, List]"
    owner = models.ForeignKey(APIdentity, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(
        choices=VisibilityType.choices, default=0, null=False
    )
    created_time = models.DateTimeField(default=timezone.now)
    edited_time = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict)
    remote_id = models.CharField(max_length=200, null=True, default=None)

    class Meta:
        abstract = True

    # MEMBER_CLASS = None  # subclass must override this
    # subclass must add this:
    # items = models.ManyToManyField(Item, through='ListMember')

    @property
    def ordered_members(self):
        return self.members.all().order_by("position", "item_id")

    @property
    def ordered_items(self):
        return self.items.all().order_by(
            self.MEMBER_CLASS.__name__.lower() + "__position"
        )

    @property
    def recent_items(self):
        return self.items.all().order_by(
            "-" + self.MEMBER_CLASS.__name__.lower() + "__created_time"
        )

    @property
    def recent_members(self):
        return self.members.all().order_by("-created_time")

    def get_member_for_item(self, item):
        return self.members.filter(item=item).first()

    def get_summary(self) -> dict[str, int]:
        summary = {k: 0 for k in ItemCategory.values}
        ctype_to_category = {
            ctype_id: getattr(cls, "category", None)
            for cls, ctype_id in item_content_types().items()
        }
        rows = (
            self.items.all()
            .values("polymorphic_ctype_id")
            .annotate(count=models.Count("id"))
        )
        for row in rows:
            category = ctype_to_category.get(row["polymorphic_ctype_id"])
            if category in summary:
                summary[category] += row["count"]
        return summary

    def append_item(self, item, **params):
        """
        named metadata fields should be specified directly, not in metadata dict!
        e.g. collection.append_item(item, note="abc") works, but collection.append_item(item, metadata={"note":"abc"}) doesn't
        """
        if item is None:
            raise ValueError("item is None")
        member = self.get_member_for_item(item)
        if member:
            return member, False
        ml = self.ordered_members
        p = {"parent": self}
        p.update(params)
        lm = ml.last()
        member = self.MEMBER_CLASS.objects.create(
            owner=self.owner,
            position=lm.position + 1 if lm else 1,
            item=item,
            **p,
        )
        list_add.send(sender=self.__class__, instance=self, item=item, member=member)
        return member, True

    def remove_item(self, item):
        member = self.get_member_for_item(item)
        if member:
            list_remove.send(
                sender=self.__class__, instance=self, item=item, member=member
            )
            member.delete()

    def update_member_order(self, ordered_member_ids):
        position_by_id = {pk: i + 1 for i, pk in enumerate(ordered_member_ids)}
        to_update = []
        for m in self.members.all():
            new_pos = position_by_id.get(m.pk)
            if new_pos is not None and m.position != new_pos:
                m.position = new_pos
                to_update.append(m)
        if to_update:
            with transaction.atomic():
                self.MEMBER_CLASS.objects.bulk_update(to_update, ["position"])
            list_add.send(sender=self.__class__, instance=self, item=None, member=None)

    def move_up_item(self, item):
        members = self.ordered_members
        member = self.get_member_for_item(item)
        if member:
            other = members.filter(position__lt=member.position).last()
            if other:
                p = other.position
                other.position = member.position
                member.position = p
                other.save()
                member.save()
                list_add.send(
                    sender=self.__class__, instance=self, item=item, member=member
                )

    def move_down_item(self, item):
        members = self.ordered_members
        member = self.get_member_for_item(item)
        if member:
            other = members.filter(position__gt=member.position).first()
            if other:
                p = other.position
                other.position = member.position
                member.position = p
                other.save()
                member.save()
                list_add.send(
                    sender=self.__class__, instance=self, item=item, member=member
                )

    def update_item_metadata(self, item, metadata):
        member = self.get_member_for_item(item)
        if member:
            member.metadata = metadata
            member.save()

    # ------------------------------------------------------------------
    # ActivityPub federation surface (shared by Collection and Shelf)
    # ------------------------------------------------------------------
    #
    # Wire shape:
    #
    #   /<list-url>                  GET application/activity+json
    #     -> {type: "Shelf", ..., totalItems, first, last}
    #
    #   /<list-url>/items            GET application/activity+json
    #     -> {type: "OrderedCollection", id, totalItems, first, last}
    #
    #   /<list-url>/items?page=N     GET application/activity+json
    #     -> {type: "OrderedCollectionPage", id, partOf,
    #         orderedItems: [...], next?, prev?}
    #
    # Each `orderedItems` entry has `type: "ShelfItem"` and at least
    # `withRegardTo` (catalog item URL). Subclass-specific extra fields
    # (e.g. `post`, `commentText`) are added in `ap_member_entry`.
    #
    # The metadata Shelf object is also embedded in the announcement
    # Note Post (under `relatedWith`) so peers can build a local mirror
    # without an immediate signed GET; pulling the items list still
    # requires the dereferenceable endpoint.

    AP_OBJECT_TYPE: str = "Shelf"

    if TYPE_CHECKING:
        # Subclasses (e.g. ``Collection`` via its own ``display_title`` and
        # ``Shelf``'s override) provide this; declare it on the base so
        # ``ap_envelope`` typechecks against ``List``.
        @property
        def display_title(self) -> str: ...

    @property
    def ap_items_url(self) -> str:
        return f"{self.absolute_url}/items"

    def ap_items_page_url(self, page: int) -> str:
        return f"{self.ap_items_url}?page={page}"

    def ap_total_items(self) -> int:
        """Total members. Subclasses with virtual / dynamic membership
        (e.g. Collection.is_dynamic) override this."""
        return self.members.count()

    def ap_member_queryset(self):
        """Ordered queryset of concrete members for paginated serialization.
        Subclasses with virtual membership (dynamic Collection) override."""
        return self.members.order_by("position", "id")

    def ap_object_extra_fields(self) -> dict[str, Any]:
        """Subclass hook for envelope fields beyond the shared shape."""
        return {}

    def ap_member_entry(self, member: "ListMember") -> dict[str, Any]:
        """Subclass hook to serialize one member into a `ShelfItem` entry.

        Must include at minimum `type: "ShelfItem"` and `withRegardTo`."""
        raise NotImplementedError

    def ap_envelope(self) -> dict[str, Any]:
        """Lightweight Shelf AP object (envelope only — no items inline).

        Returned by both the announcement Note Post (embedded in
        `relatedWith`) and the dereferenceable `<list-url>` endpoint
        after visibility check. Items live behind `first`/`last`.
        """
        total = self.ap_total_items()
        page_count = max(1, (total + AP_PAGE_SIZE - 1) // AP_PAGE_SIZE) if total else 1
        envelope: dict[str, Any] = {
            "id": self.absolute_url,
            "type": self.AP_OBJECT_TYPE,
            "name": self.display_title,
            "mediaType": "text/markdown",
            "published": self.created_time.isoformat(),
            "updated": self.edited_time.isoformat(),
            "attributedTo": self.owner.actor_uri,
            "href": self.absolute_url,
            "totalItems": total,
            "first": self.ap_items_page_url(1),
            "last": self.ap_items_page_url(page_count),
        }
        envelope.update(self.ap_object_extra_fields())
        return envelope

    def ap_items_envelope(self) -> dict[str, Any]:
        total = self.ap_total_items()
        page_count = max(1, (total + AP_PAGE_SIZE - 1) // AP_PAGE_SIZE) if total else 1
        return {
            "id": self.ap_items_url,
            "type": "OrderedCollection",
            "totalItems": total,
            "first": self.ap_items_page_url(1),
            "last": self.ap_items_page_url(page_count),
        }

    def ap_items_page(self, page: int) -> dict[str, Any]:
        total = self.ap_total_items()
        page_count = max(1, (total + AP_PAGE_SIZE - 1) // AP_PAGE_SIZE) if total else 1
        if page < 1 or page > page_count:
            ordered = []
        else:
            offset = (page - 1) * AP_PAGE_SIZE
            qs = self.ap_member_queryset()
            members = list(qs[offset : offset + AP_PAGE_SIZE])
            # Item is polymorphic; select_related("item") loses subclass info,
            # so re-fetch through the polymorphic manager and re-attach.
            item_ids = [m.item_id for m in members if m.item_id]
            if item_ids:
                items_map = {it.pk: it for it in Item.objects.filter(pk__in=item_ids)}
                for m in members:
                    m.item = items_map.get(m.item_id) or m.item
            ordered = [self.ap_member_entry(m) for m in members if m.item is not None]
        page_obj: dict[str, Any] = {
            "id": self.ap_items_page_url(page),
            "type": "OrderedCollectionPage",
            "partOf": self.ap_items_url,
            "orderedItems": ordered,
        }
        if 1 < page <= page_count:
            page_obj["prev"] = self.ap_items_page_url(page - 1)
        if 1 <= page < page_count:
            page_obj["next"] = self.ap_items_page_url(page + 1)
        return page_obj

    # --- Inbound side ---------------------------------------------------

    @classmethod
    def params_from_envelope(cls, obj: dict[str, Any]) -> dict[str, Any]:
        """Subclass hook: translate an inbound Shelf envelope into a dict
        of model-field values for ``__init__``/``setattr``."""
        return {}

    @classmethod
    def fetch_remote_member_url(cls) -> str:
        """Per-class import path for the page-walking job. Subclasses set this
        if they want async member fetching; default disables it."""
        return "journal.jobs.list_sync.fetch_remote_list_members"

    @classmethod
    def existing_for_envelope(cls, owner, obj: dict[str, Any], post):
        """Subclass hook: locate an existing local row for an inbound
        envelope. Default keys on the announcement post id (works for
        Collection); subclasses with a stable natural key (Shelf's
        ``(owner, shelf_type)``) override to also match by that key so a
        re-announcement from the same actor doesn't try to insert a
        duplicate row that would fail unique constraints.
        """
        return cls.get_by_post_id(post.id) if post else None

    @classmethod
    def update_by_ap_envelope(cls, owner, obj: dict[str, Any], post) -> "List | None":
        """Inbound mirror builder. Validates the envelope, persists / updates
        the local mirror keyed by `remote_id`, and enqueues a member fetch.

        Returns the persisted instance or None on rejection.
        """
        existing = cls.existing_for_envelope(owner, obj, post)
        if existing and existing.owner.pk != post.author_id:
            logger.warning(
                f"{cls.__name__} owner mismatch on inbound: "
                f"{existing.owner.pk} != {post.author_id}"
            )
            return None
        # SSRF guard: the envelope `id` becomes the local `remote_id` and is
        # later signed-GET'd by the page-walking sync. Validate before
        # persisting and require its host to match the announcing author.
        list_id = obj.get("id")
        if not is_valid_url(list_id):
            logger.warning(f"{cls.__name__} inbound rejected: bad id URL {list_id!r}")
            return None
        author_actor_uri = getattr(getattr(post, "author", None), "actor_uri", None)
        if author_actor_uri:
            if urlparse(list_id).hostname != urlparse(author_actor_uri).hostname:
                logger.warning(
                    f"{cls.__name__} inbound rejected: id host {list_id!r} "
                    f"does not match author host {author_actor_uri!r}"
                )
                return None
        edited = parse_datetime(obj.get("updated") or obj.get("published") or "")
        published = parse_datetime(obj.get("published") or "")
        if (
            not edited
            or not published
            or timezone.is_naive(edited)
            or timezone.is_naive(published)
        ):
            logger.warning(
                f"{cls.__name__} inbound rejected: bad datetime in {list_id}"
            )
            return None
        if existing and existing.edited_time >= edited:
            return existing
        visibility = Takahe.visibility_t2n(post.visibility)
        fields = cls.params_from_envelope(obj)
        fields["visibility"] = visibility
        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.save(
                update_fields=list(fields.keys()),
                post_when_save=False,
                index_when_save=False,
            )
            inst = existing
        else:
            # Subclass-specific kwargs flow through `fields`; require unique
            # ones (Shelf needs shelf_type).
            inst = cls(
                owner=owner,
                local=False,
                remote_id=list_id,
                created_time=published,
                **fields,
            )
            inst.save(post_when_save=False, index_when_save=False)
            inst.link_post_id(post.pk)
        # `edited_time` is `auto_now=True`; bypass it with a queryset update
        # so future staleness checks compare against the remote's `updated`.
        cls.objects.filter(pk=inst.pk).update(edited_time=edited)
        inst.edited_time = edited
        try:
            django_rq.get_queue("fetch").enqueue(
                cls.fetch_remote_member_url(),
                cls.__module__ + "." + cls.__name__,
                inst.pk,
            )
        except Exception as e:
            logger.warning(
                f"Failed to enqueue {cls.__name__} member fetch for {inst.pk}: {e}"
            )
        return inst


class ListMember(Piece):
    """
    ListMember - List class's member class
    It's an abstract class, subclass must add this:

    parent = models.ForeignKey('List', related_name='members', on_delete=models.CASCADE)
    """

    if TYPE_CHECKING:
        parent: models.ForeignKey["ListMember", "List"]
        item_id: int
    owner = models.ForeignKey(APIdentity, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(
        choices=VisibilityType.choices, default=0, null=False
    )
    created_time = models.DateTimeField(default=timezone.now)
    edited_time = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    position = models.PositiveIntegerField()

    @cached_property
    def mark(self):
        from .mark import Mark

        m = Mark(self.owner, self.item)
        return m

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.__class__.__name__}:{self.pk}[{self.parent}]:{self.item}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # trigger index update if mark count or tag changes
        if self.item:
            self.item.update_index(later=True)

    def delete(self, *args, **kwargs):
        r = super().delete(*args, **kwargs)
        # trigger index update if mark count or tag changes
        if self.item:
            self.item.update_index(later=True)
        return r
