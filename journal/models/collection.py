import re
from functools import cached_property
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.paginator import Paginator
from django.db import models, transaction
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from loguru import logger

from catalog.models import CatalogCollection, Item
from catalog.models.utils import piece_cover_path
from catalog.search.utils import enqueue_fetch
from common.models import jsondata
from common.utils import get_file_absolute_url
from journal.search import JournalIndex, JournalQueryParser
from takahe.utils import Takahe
from users.models import APIdentity

from .common import Piece
from .itemlist import AP_PAGE_SIZE, List, ListMember, list_add, list_remove
from .renderers import render_md, render_text

_RE_HTML_TAG = re.compile(r"<[^>]*>")


class CollectionMember(ListMember):
    parent = models.ForeignKey(
        "Collection", related_name="members", on_delete=models.CASCADE
    )

    note = jsondata.CharField(_("note"), null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "item"], name="unique_collection_member"
            ),
        ]

    @property
    def note_html(self) -> str:
        return render_text(self.note) if self.note else ""

    @property
    def ap_object(self):
        # `commentText` (was `note` in pre-rename versions) carries the
        # user's per-collection note. `note` collided with the AS Note
        # type and was confusing in code review.
        return {
            "id": self.absolute_url,
            "type": "ShelfItem",
            "collection": self.parent.absolute_url,
            "published": self.created_time.isoformat(),
            "updated": self.edited_time.isoformat(),
            "attributedTo": self.owner.actor_uri,
            "withRegardTo": self.item.absolute_url,
            "commentText": self.note or "",
            "href": self.absolute_url,
        }

    def to_indexable_doc(self) -> dict[str, Any]:
        return {}


class Collection(List):
    if TYPE_CHECKING:
        members: models.QuerySet[CollectionMember]
        _stats_cache: dict[int, dict[str, int]]
    url_path = "collection"
    post_when_save = True
    index_when_save = True
    MEMBER_CLASS = CollectionMember
    # Nullable so remote (mirror) collections don't get a sentinel
    # ``CatalogCollection`` row. ``Collection.save`` only auto-creates and
    # syncs the catalog item when ``self.local``.
    catalog_item = models.OneToOneField(
        CatalogCollection,
        on_delete=models.PROTECT,
        related_name="journal_item",
        null=True,
        blank=True,
    )
    title = models.CharField(_("title"), max_length=1000, default="")
    brief = models.TextField(_("description"), blank=True, default="")
    cover = models.ImageField(
        upload_to=piece_cover_path, default=settings.DEFAULT_ITEM_COVER, blank=True
    )
    items = models.ManyToManyField(
        Item, through="CollectionMember", related_name="collections"
    )
    collaborative = models.PositiveSmallIntegerField(
        default=0
    )  # 0: Editable by owner only / 1: Editable by bi-direction followers
    featured_by = models.ManyToManyField(
        to=APIdentity, related_name="featured_collections", through="FeaturedCollection"
    )
    query = models.CharField(
        _("search query for dynamic collection"),
        max_length=1000,
        blank=True,
        default=None,
        null=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["remote_id"], name="collection_remote_id_idx"),
        ]

    def __str__(self):
        return f"Collection:{self.uuid}@{self.owner_id}:{self.title}"

    @property
    def cover_image_url(self) -> str | None:
        return get_file_absolute_url(self.cover)

    @property
    def is_dynamic(self):
        return self.query is not None

    @property
    def trackable(self):
        if self.is_dynamic:
            return len(self.item_ids) > 0
        else:
            return self.query_result and self.query_result.pages == 1

    @property
    def html_content(self):
        html = render_md(self.brief)
        return html

    @property
    def plain_content(self):
        html = render_md(self.brief)
        return _RE_HTML_TAG.sub(" ", html)

    def featured_since(self, owner: APIdentity):
        f = FeaturedCollection.objects.filter(target=self, owner=owner).first()
        return f.created_time if f else None

    def get_query(self, viewer, **kwargs):
        if not self.is_dynamic:
            return None
        q = JournalQueryParser(self.query, **kwargs)
        q.filter_by_owner_viewer(self.owner, viewer)
        q.filter("item_id", ">0")
        q.facet_by = ["item_class"]
        return q

    @cached_property
    def query_result(self):
        if self.is_dynamic:
            q = self.get_query(self.owner, page_size=250)
            if q:
                index = JournalIndex.instance()
                return index.search(q)
        return None

    def get_item_ids(self):
        if self.is_dynamic:
            r = self.query_result
            return [i.pk for i in r.items] if r else []
        else:
            return list(self.members.all().values_list("item_id", flat=True))

    @cached_property
    def item_ids(self):
        return self.get_item_ids()

    def get_members_by_page(
        self,
        page_number: int,
        page_size: int = 20,
        viewer: APIdentity | None = None,
        comment_as_note: bool = False,
    ):
        from .comment import Comment
        from .common import q_owned_piece_visible_to_user
        from .mark import Mark
        from .rating import Rating
        from .tag import Tag

        if self.is_dynamic:
            # Dynamic collections: use existing search-based pagination
            q = self.get_query(viewer, page=page_number)
            members = []
            pages = 0
            if q:
                r = JournalIndex.instance().search(q)
                items = r.items
                pages = r.pages
                Item.prefetch_parent_items(items)
                Item.prefetch_credits(items)
                Rating.attach_to_items(items)
                Tag.attach_to_items(items)
                if viewer:
                    Mark.attach_to_items(viewer, items, viewer.user)
                members = [{"item": i, "parent": self} for i in items]
                if comment_as_note:
                    # Attach comments from owner as collection note if viewer is not owner
                    q = q_owned_piece_visible_to_user(
                        viewer.user if viewer else None, self.owner
                    )
                    comments = {
                        c.item_id: c.text
                        for c in Comment.objects.filter(item__in=items).filter(q)
                    }
                    for m in members:
                        m["note"] = comments.get(m["item"].pk, "")
        else:
            all_members = self.ordered_members
            # .select_related("item") not working yet in django-polymorphic
            p = Paginator(all_members, page_size)
            members = p.get_page(page_number)
            pages = p.num_pages
            item_ids = [m.item_id for m in members]
            items = list(
                Item.objects.filter(pk__in=item_ids).prefetch_related(
                    "external_resources"
                )
            )
            # All members share this Collection as their parent. Setting it on
            # each instance avoids a per-member parent FK load (and consequent
            # owner/identity/domain dereferences) in collection_items.html.
            for member in members:
                member.parent = self
            if items:
                items_map = {i.pk: i for i in items}
                for member in members:
                    member.item = items_map.get(member.item_id)
                Item.prefetch_parent_items(items)
                Item.prefetch_credits(items)
                Rating.attach_to_items(items)
                Tag.attach_to_items(items)
                if viewer:
                    Mark.attach_to_items(viewer, items, viewer.user)
        return members, pages

    def get_stats(self, viewer: APIdentity):
        from .shelf import ShelfMember, ShelfType

        cached = getattr(self, "_stats_cache", {}).get(getattr(viewer, "pk", None))
        if cached is not None:
            return cached
        items = self.item_ids
        stats: dict[str, int] = {"total": len(items)}
        for st in ShelfType.values:
            stats[st] = 0
        counts = (
            ShelfMember.objects.filter(owner=viewer, item_id__in=items)
            .values("parent__shelf_type")
            .annotate(count=models.Count("id"))
        )
        for row in counts:
            stats[row["parent__shelf_type"]] = row["count"]
        stats["percentage"] = (
            round(stats["complete"] * 100 / stats["total"]) if stats["total"] else 0
        )
        return stats

    @classmethod
    def attach_stats_for_viewer(
        cls, collections: list["Collection"], viewer: APIdentity
    ) -> None:
        """Pre-compute ``get_stats(viewer)`` for many collections in two queries.

        ``_sidebar.html`` iterates ``identity.featured_collections`` and calls
        ``get_stats`` once per collection; without batching, each call hits
        ``CollectionMember`` and ``ShelfMember`` separately. This loads all
        member item_ids and the viewer's shelf placements in a single round
        trip each, then stores the per-collection stats on the instance for
        ``get_stats`` to return on cache hit.
        """
        from .shelf import ShelfMember, ShelfType

        if not collections or viewer is None:
            return
        # Static collections share their item ids via CollectionMember; dynamic
        # ones compute item_ids from the search index, so we let those fall
        # through to per-instance get_stats.
        static = [c for c in collections if not c.is_dynamic]
        items_by_collection: dict[int, list[int]] = {c.pk: [] for c in static}
        if static:
            rows = CollectionMember.objects.filter(
                parent_id__in=[c.pk for c in static]
            ).values_list("parent_id", "item_id")
            for parent_id, item_id in rows:
                items_by_collection.setdefault(parent_id, []).append(item_id)
        all_item_ids = {iid for ids in items_by_collection.values() for iid in ids}
        # One ShelfMember per (owner, item) within standard shelves, so a
        # single map of item_id -> shelf_type lets us tally per collection.
        shelf_by_item: dict[int, str] = {}
        if all_item_ids:
            for row in ShelfMember.objects.filter(
                owner=viewer, item_id__in=all_item_ids
            ).values("item_id", "parent__shelf_type"):
                shelf_by_item[row["item_id"]] = row["parent__shelf_type"]
        for c in static:
            items = items_by_collection.get(c.pk, [])
            stats: dict[str, int] = {"total": len(items)}
            for st in ShelfType.values:
                stats[st] = 0
            for item_id in items:
                st = shelf_by_item.get(item_id)
                if st:
                    stats[st] = stats.get(st, 0) + 1
            stats["percentage"] = (
                round(stats.get("complete", 0) * 100 / stats["total"])
                if stats["total"]
                else 0
            )
            cache = getattr(c, "_stats_cache", None)
            if cache is None:
                cache = {}
                c._stats_cache = cache
            cache[viewer.pk] = stats

    def get_progress(self, viewer: APIdentity):
        items = self.item_ids
        if len(items) == 0:
            return 0
        shelf = viewer.shelf_manager.shelf_list["complete"]
        return round(
            shelf.members.all().filter(item_id__in=items).count() * 100 / len(items)
        )

    def get_summary(self):
        if self.is_dynamic:
            r = self.query_result
            return r.facet_by_category if r and self.trackable else {}
        else:
            return super().get_summary()

    @cached_property
    def item_count_by_category(self) -> dict[str, int]:
        from catalog.models import ItemCategory

        summary = self.get_summary()
        return {cat.value: int(summary.get(cat.value) or 0) for cat in ItemCategory}

    @classmethod
    def attach_item_count_by_category(cls, collections: list["Collection"]) -> None:
        """Pre-compute ``item_count_by_category`` for many static collections in one query.

        Without this, serializing a list of Collections triggers one
        per-collection ``get_summary()`` query (N+1). Dynamic collections
        each issue a distinct search and are left to per-instance
        ``cached_property`` resolution.
        """
        from catalog.models import ItemCategory
        from catalog.models.item import item_content_types

        if not collections:
            return
        static = [c for c in collections if not c.is_dynamic]
        if not static:
            return
        ctype_to_category = {
            ctype_id: getattr(cls_, "category", None)
            for cls_, ctype_id in item_content_types().items()
        }
        all_cats = {cat.value for cat in ItemCategory}
        counts_by_collection: dict[int, dict[str, int]] = {
            c.pk: {cat.value: 0 for cat in ItemCategory} for c in static
        }
        rows = (
            CollectionMember.objects.filter(parent_id__in=[c.pk for c in static])
            .values("parent_id", "item__polymorphic_ctype_id")
            .annotate(count=models.Count("id"))
        )
        for row in rows:
            category = ctype_to_category.get(row["item__polymorphic_ctype_id"])
            if category and category in all_cats:
                counts_by_collection[row["parent_id"]][category] += row["count"]
        for c in static:
            # Seed ``cached_property`` storage so the descriptor returns
            # the precomputed dict without re-running get_summary().
            c.__dict__["item_count_by_category"] = counts_by_collection[c.pk]

    def save(self, *args, **kwargs):
        # Remote mirrors don't need a CatalogCollection — those rows would
        # otherwise pollute the catalog with stub entries that have no
        # corresponding local catalog detail page.
        if self.local:
            if getattr(self, "catalog_item", None) is None:
                self.catalog_item = CatalogCollection()
            if (
                self.catalog_item.title != self.title
                or self.catalog_item.brief != self.brief
            ):
                self.catalog_item.title = self.title
                self.catalog_item.brief = self.brief
                self.catalog_item.cover = self.cover
                self.catalog_item.save()
        super().save(*args, **kwargs)

    def get_ap_data(self):
        return {
            "object": {
                # "tag": [item.ap_object_ref for item in collection.items],
                "relatedWith": [self.ap_object],
            }
        }

    def sync_to_timeline(self, update_mode: int = 0):
        existing_post = self.latest_post
        owner: APIdentity = self.owner
        user = owner.user
        v = Takahe.visibility_n2t(self.visibility, user.preference.post_public_mode)
        if existing_post and (update_mode == 1 or v != existing_post.visibility):
            Takahe.delete_posts([existing_post.pk])
            existing_post = None
        data = self.get_ap_data()
        # if existing_post and existing_post.type_data == data:
        #     return existing_post
        action = _("created a collection")
        item_link = self.absolute_url
        prepend_content = f'{action} <a href="{item_link}">{self.title}</a><br>'
        content = self.plain_content
        if len(content) > 360:
            content = content[:357] + "..."
        post = Takahe.post(
            self.owner.pk,
            content,
            v,
            prepend_content,
            "",
            None,
            False,
            data,
            existing_post.pk if existing_post else None,
            self.created_time,
            language=owner.user.macrolanguage,
            application_id=self.application_id_when_save,
        )
        if post and post != existing_post:
            self.link_post_id(post.pk)
        return post

    # --- ActivityPub: outbound -----------------------------------------

    def ap_total_items(self) -> int:
        if self.is_dynamic:
            r = self.query_result
            return len(list(r.items)) if r else 0
        return self.members.count()

    def ap_member_queryset(self):
        # Dynamic collections snapshot the current search result; each page
        # call materializes the same in-memory list. Receivers see it as a
        # static ordered list.
        if self.is_dynamic:
            r = self.query_result
            items = list(r.items) if r else []
            # Wrap each Item in a transient CollectionMember so the shared
            # paginator can re-use the same per-entry serializer hook.
            return [
                CollectionMember(
                    parent=self,
                    item=item,
                    owner=self.owner,
                    position=i + 1,
                    note=None,
                )
                for i, item in enumerate(items)
            ]
        return self.members.order_by("position", "id")

    def ap_object_extra_fields(self) -> dict[str, Any]:
        extras: dict[str, Any] = {"content": self.brief or ""}
        if self.is_dynamic:
            extras["query"] = self.query
        return extras

    def ap_member_entry(self, member: ListMember) -> dict[str, Any]:
        # ``member`` is always a ``CollectionMember`` here (the list's
        # MEMBER_CLASS), but the signature follows the base class contract.
        assert isinstance(member, CollectionMember)
        entry: dict[str, Any] = {
            "type": "ShelfItem",
            "withRegardTo": member.item.absolute_url,
        }
        if member.note:
            entry["commentText"] = member.note
        return entry

    @property
    def ap_object(self) -> dict[str, Any]:
        """Lightweight Shelf AP envelope embedded in the announcement Note.

        Members are NOT included here — they live behind ``first``/``last``
        which point at the paginated items endpoint. Receivers materialize
        members by walking the page chain with signed GETs.
        """
        return self.ap_envelope()

    # --- ActivityPub: inbound ------------------------------------------

    @classmethod
    def params_from_envelope(cls, obj: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": obj.get("name") or "",
            "brief": obj.get("content") or "",
        }

    @classmethod
    def params_from_ap_object(cls, post, obj, piece):
        # Collections override ``update_by_ap_object`` directly because member
        # resolution is N-ary; this base hook is unused but kept to satisfy the
        # ``Piece`` abstract API.
        return {}

    @classmethod
    def update_by_ap_object(cls, owner, item, obj, post, crosspost=None):
        """Inbound federation hook for Collection (Shelf-shaped envelope).

        Delegates to the shared ``List.update_by_ap_envelope`` to build /
        update the local mirror, then enqueues a paginated member fetch.

        ``item`` is unused for Collection but kept in the signature so
        ``takahe/ap_handlers.py:_post_fetched`` can dispatch uniformly.
        """
        return cls.update_by_ap_envelope(owner, obj, post)

    @classmethod
    def _sync_members_from_ap(
        cls, col: "Collection", item_objs: list[dict[str, Any]]
    ) -> int:
        """Apply a flat ``orderedItems`` list onto local ``CollectionMember`` rows.

        The page-walking job in ``journal.jobs.list_sync`` collects every page
        before calling this once with the concatenated items. Per-page entry
        cap (``AP_PAGE_SIZE``) is enforced upstream by the page paginator on
        the sender and re-checked here defensively.

        Returns the number of items that could not be resolved locally; each
        triggers an async catalog fetch. The caller decides whether to
        re-run on retry.
        """
        if not isinstance(item_objs, list):
            return 0
        # Filter to the wire type and dict-shaped entries; bound size by a
        # generous soft cap to prevent pathological memory use.
        MAX_TOTAL_ITEMS = AP_PAGE_SIZE * 1000  # 100k items soft cap
        item_objs = [
            e for e in item_objs if isinstance(e, dict) and e.get("type") == "ShelfItem"
        ][:MAX_TOTAL_ITEMS]
        resolved: list[tuple[Item, dict[str, Any]]] = []
        pending = 0
        for entry in item_objs:
            url = entry.get("withRegardTo")
            if not url:
                continue
            looked_up = Item.get_by_remote_url(url)
            if looked_up:
                resolved.append((looked_up, entry))
                continue
            pending += 1
            try:
                enqueue_fetch(url, is_refetch=False, user=None)
            except Exception as e:
                logger.warning(f"Failed to enqueue fetch for {url}: {e}")
        with transaction.atomic():
            cls.objects.select_for_update().filter(pk=col.pk).first()
            existing_members = {m.item_id: m for m in col.members.all()}
            kept: set[int] = set()
            to_update: list[CollectionMember] = []
            for pos, (it, entry) in enumerate(resolved, start=1):
                if it.pk in kept:
                    continue
                kept.add(it.pk)
                note = entry.get("commentText") or entry.get("note") or None
                m = existing_members.get(it.pk)
                if m is None:
                    CollectionMember.objects.create(
                        parent=col,
                        item=it,
                        owner=col.owner,
                        position=pos,
                        note=note,
                    )
                elif m.note != note or m.position != pos:
                    m.note = note
                    m.position = pos
                    to_update.append(m)
            if to_update:
                CollectionMember.objects.bulk_update(
                    to_update, ["metadata", "position"]
                )
            stale_ids = [
                m.pk for item_id, m in existing_members.items() if item_id not in kept
            ]
            if stale_ids:
                CollectionMember.objects.filter(pk__in=stale_ids).delete()
        return pending

    def to_indexable_doc(self) -> dict[str, Any]:
        content = [self.title, self.brief]
        item_id = []
        item_title = []
        item_class = set()
        for m in self.members.all():
            item_id.append(m.item.pk)
            item_title += m.item.to_indexable_titles()
            item_class |= {m.item.__class__.__name__}
            if m.note:
                content.append(m.note)
        return {
            "item_id": item_id,
            "item_class": list(item_class),
            "item_title": item_title,
            "content": content,
        }

    @property
    def display_title(self) -> str:
        return self.title


@receiver(list_add)
@receiver(list_remove)
def _collection_member_changed(sender, instance, item, member, **kwargs):
    """Re-save local Collection when its member set changes so that
    ``sync_to_timeline`` re-posts an updated AP payload (which now embeds the
    ordered member list). For remote mirrors, ``Piece.save`` already gates
    timeline sync on ``self.local``.
    """
    if not isinstance(instance, Collection):
        return
    if not instance.local:
        return
    instance.save()


class FeaturedCollection(Piece):
    owner = models.ForeignKey(APIdentity, on_delete=models.CASCADE)
    target = models.ForeignKey(Collection, on_delete=models.CASCADE)
    created_time = models.DateTimeField(auto_now_add=True)
    edited_time = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [["owner", "target"]]

    @property
    def visibility(self):
        return self.target.visibility

    @cached_property
    def progress(self):
        return self.target.get_progress(self.owner)

    def to_indexable_doc(self) -> dict[str, Any]:
        return {}
