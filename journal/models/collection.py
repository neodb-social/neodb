import re
from functools import cached_property
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import django_rq
from django.conf import settings
from django.core.paginator import Paginator
from django.db import models, transaction
from django.dispatch import receiver
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from loguru import logger

from catalog.models import CatalogCollection, Item
from catalog.models.utils import piece_cover_path
from catalog.search.utils import enqueue_fetch
from common.models import jsondata
from common.utils import get_file_absolute_url
from common.validators import is_valid_url
from journal.search import JournalIndex, JournalQueryParser
from takahe.utils import Takahe
from users.models import APIdentity

from .common import Piece
from .itemlist import List, ListMember, list_add, list_remove
from .renderers import render_md, render_text

_RE_HTML_TAG = re.compile(r"<[^>]*>")

# Cap on the number of members included in the federated Collection AP object.
# Larger lists are truncated (sender side); receivers honor whatever they get.
MAX_AP_COLLECTION_ITEMS = 250


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
        return {
            "id": self.absolute_url,
            "type": "CollectionItem",
            "collection": self.parent.absolute_url,
            "published": self.created_time.isoformat(),
            "updated": self.edited_time.isoformat(),
            "attributedTo": self.owner.actor_uri,
            "withRegardTo": self.item.absolute_url,
            "note": self.note,
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
    catalog_item = models.OneToOneField(
        CatalogCollection, on_delete=models.PROTECT, related_name="journal_item"
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

    def save(self, *args, **kwargs):
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
        action = _("created collection")
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

    def _ap_object_base(self, total_items: int) -> dict[str, Any]:
        return {
            "id": self.absolute_url,
            "type": "Collection",
            "name": self.title,
            "content": self.brief,
            "mediaType": "text/markdown",
            "published": self.created_time.isoformat(),
            "updated": self.edited_time.isoformat(),
            "attributedTo": self.owner.actor_uri,
            "href": self.absolute_url,
            "totalItems": total_items,
        }

    @property
    def ap_object(self):
        """Lightweight AP object embedded in the announcement Note Post.

        The full ordered member list is served only by the dereferenceable
        AP endpoint at ``self.absolute_url`` (signed GET), so receivers
        must follow up with their own signed fetch to materialize members.
        ``totalItems`` is included so receivers can show a count without
        fetching, but ``orderedItems`` is intentionally absent.

        ``totalItems`` is computed from a count query (no member load) for
        non-dynamic collections; dynamic collections snapshot 0 here because
        their item list is generated lazily by the dereferenceable endpoint.
        """
        if self.is_dynamic:
            total = 0
        else:
            total = min(self.members.count(), MAX_AP_COLLECTION_ITEMS)
        return self._ap_object_base(total)

    def full_ap_object(self) -> dict[str, Any]:
        """Dereferenceable AP object including ordered members.

        Returned by the ``/collection/<uuid>`` AP endpoint after the caller's
        HTTP signature is verified and ``is_visible_to_identity`` passes.
        """
        ordered_items = self._members_ap_payload()
        obj = self._ap_object_base(len(ordered_items))
        obj["orderedItems"] = ordered_items
        return obj

    def _members_ap_payload(self) -> list[dict[str, Any]]:
        """Serialize members for ``orderedItems`` in the Collection AP object.

        Dynamic collections snapshot their current query result at post time;
        the receiving server treats it as a static list. The list is capped
        at ``MAX_AP_COLLECTION_ITEMS`` to bound payload size; ``totalItems``
        in ``ap_object`` reflects the truncated count rather than the full
        member set.
        """
        if self.is_dynamic:
            r = self.query_result
            items = list(r.items) if r else []
            items = items[:MAX_AP_COLLECTION_ITEMS]
            return [
                {
                    "type": "CollectionItem",
                    "withRegardTo": item.absolute_url,
                    "itemType": item.__class__.__name__,
                    "note": "",
                }
                for item in items
            ]
        members = list(
            self.members.order_by("position", "id")[:MAX_AP_COLLECTION_ITEMS]
        )
        # ``Item`` is polymorphic, so ``select_related("item")`` returns the
        # base ``Item`` class with the wrong ``url_path``. Fetch through
        # the polymorphic manager and re-attach.
        item_ids = [m.item_id for m in members]
        items_map = {it.pk: it for it in Item.objects.filter(pk__in=item_ids)}
        for m in members:
            m.item = items_map.get(m.item_id)
        return [
            {
                "id": m.absolute_url,
                "type": "CollectionItem",
                "withRegardTo": m.item.absolute_url,
                "itemType": m.item.__class__.__name__,
                "note": m.note or "",
            }
            for m in members
            if m.item is not None
        ]

    @classmethod
    def params_from_ap_object(cls, post, obj, piece):
        # Collections override ``update_by_ap_object`` directly because member
        # resolution is N-ary; this base hook is unused but kept to satisfy the
        # ``Piece`` abstract API.
        return {}

    @classmethod
    def update_by_ap_object(cls, owner, item, obj, post, crosspost=None):
        """Inbound federation hook for Collection.

        Builds (or updates) the local mirror from the metadata embedded in
        the announcement Note, then schedules a separate signed GET against
        the Collection's ``id`` URL to materialize members. The Note never
        carries the full member list — see ``Collection.ap_object`` and
        ``Collection.full_ap_object`` for the two shapes.

        ``item`` is unused for Collection but kept in the signature so
        ``takahe/ap_handlers.py:_post_fetched`` can dispatch uniformly.
        """
        existing = cls.get_by_post_id(post.id)
        if existing and existing.owner.pk != post.author_id:
            logger.warning(
                f"Collection owner mismatch on inbound: {existing.owner.pk} != {post.author_id}"
            )
            return
        # SSRF guard: the Collection's ``id`` URL becomes ``remote_id`` and is
        # later signed-GET'd by ``fetch_remote_collection_members``. Validate
        # it before persisting and require its host to match the announcing
        # author's host so a peer cannot redirect our worker to arbitrary
        # internal/blocked URLs by claiming any ``id``.
        collection_id = obj.get("id")
        if not is_valid_url(collection_id):
            logger.warning(f"Collection inbound rejected: bad id URL {collection_id!r}")
            return None
        author_actor_uri = getattr(getattr(post, "author", None), "actor_uri", None)
        if author_actor_uri:
            if urlparse(collection_id).hostname != urlparse(author_actor_uri).hostname:
                logger.warning(
                    f"Collection inbound rejected: id host {collection_id!r} "
                    f"does not match author host {author_actor_uri!r}"
                )
                return None
        # ``parse_datetime`` tolerates ISO-8601 variants ("Z" suffix, missing
        # colon in offset). Reject naive datetimes — comparing them against
        # tz-aware ``edited_time`` raises under ``USE_TZ=True``.
        edited = parse_datetime(obj.get("updated") or obj.get("published") or "")
        published = parse_datetime(obj.get("published") or "")
        if (
            not edited
            or not published
            or timezone.is_naive(edited)
            or timezone.is_naive(published)
        ):
            logger.warning(
                f"Collection inbound rejected: bad datetime in {collection_id}"
            )
            return None
        if existing and existing.edited_time >= edited:
            return existing
        visibility = Takahe.visibility_t2n(post.visibility)
        fields = {
            "title": obj.get("name") or "",
            "brief": obj.get("content") or "",
            "visibility": visibility,
        }
        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.save(
                update_fields=list(fields.keys()),
                post_when_save=False,
                index_when_save=False,
            )
            col = existing
        else:
            col = cls(
                owner=owner,
                local=False,
                remote_id=collection_id,
                created_time=published,
                **fields,
            )
            col.save(post_when_save=False, index_when_save=False)
            col.link_post_id(post.pk)
        # ``edited_time`` is ``auto_now=True`` on ``List``; ``save()`` would
        # stamp it with ``now()`` regardless of any value we assign. Bypass
        # ``auto_now`` with a direct queryset update so future staleness
        # checks compare against the remote's ``updated`` timestamp.
        cls.objects.filter(pk=col.pk).update(edited_time=edited)
        col.edited_time = edited
        cls._enqueue_member_fetch(col)
        return col

    @classmethod
    def _enqueue_member_fetch(cls, col: "Collection") -> None:
        try:
            django_rq.get_queue("fetch").enqueue(
                "journal.jobs.collection_sync.fetch_remote_collection_members",
                col.pk,
            )
        except Exception as e:
            logger.warning(f"Failed to enqueue member fetch for {col.pk}: {e}")

    @classmethod
    def _sync_members_from_ap(
        cls, col: "Collection", item_objs: list[dict[str, Any]]
    ) -> int:
        """Materialize ``orderedItems`` into ``CollectionMember`` rows.

        Returns the number of items that could not be resolved locally (each
        triggers an async catalog fetch). The caller is responsible for
        retrying the upstream signed GET if it cares about eventual
        completeness.

        Inbound entries are capped at ``MAX_AP_COLLECTION_ITEMS`` (the same
        limit the sender applies) so a malicious peer cannot drive
        unbounded DB work or fetch enqueueing by returning a huge list.
        Non-list / wrong-type entries are filtered out.
        """
        if not isinstance(item_objs, list):
            return 0
        item_objs = [
            e
            for e in item_objs
            if isinstance(e, dict) and e.get("type") == "CollectionItem"
        ][:MAX_AP_COLLECTION_ITEMS]
        resolved: list[tuple[Item, dict[str, Any]]] = []
        pending = 0
        for entry in item_objs:
            url = entry.get("withRegardTo")
            if not url:
                continue
            # Local-only lookup. ``get_by_ap_object`` would do synchronous
            # network I/O via ``SiteManager`` to fetch missing items; doing
            # that for up to ``MAX_AP_COLLECTION_ITEMS`` entries in a
            # background job risks long blocks. Defer fetches to the
            # ``enqueue_fetch`` queue and let the resync job retry once
            # items have been cached.
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
            # ``select_for_update`` on the parent serializes concurrent member
            # syncs (inbound update + URL paste + retries) so two jobs do not
            # race to insert duplicate ``(parent, item)`` rows. The unique
            # constraint on ``CollectionMember`` is the belt under this
            # suspender.
            cls.objects.select_for_update().filter(pk=col.pk).first()
            existing_members = {m.item_id: m for m in col.members.all()}
            kept: set[int] = set()
            to_update: list[CollectionMember] = []
            for pos, (it, entry) in enumerate(resolved, start=1):
                if it.pk in kept:
                    # Defensive: a peer could repeat the same item; skip.
                    continue
                kept.add(it.pk)
                note = entry.get("note") or None
                m = existing_members.get(it.pk)
                if m is None:
                    # New rows go through ``create`` so PolymorphicModel can
                    # populate ``polymorphic_ctype`` and run signals.
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
            # ``note`` is a jsondata.CharField stored inside ``metadata``;
            # writing the JSON column is what persists the note value.
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
