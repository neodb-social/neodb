import re
from functools import cached_property
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.paginator import Paginator
from django.db import models
from django.utils.translation import gettext_lazy as _

from catalog.collection.models import Collection as CatalogCollection
from catalog.common import jsondata
from catalog.common.utils import piece_cover_path
from catalog.models import Item
from journal.models.index import JournalIndex, JournalQueryParser
from takahe.utils import Takahe
from users.models import APIdentity

from .common import Piece
from .itemlist import List, ListMember
from .renderers import render_md

_RE_HTML_TAG = re.compile(r"<[^>]*>")


class CollectionMember(ListMember):
    parent = models.ForeignKey(
        "Collection", related_name="members", on_delete=models.CASCADE
    )

    note = jsondata.CharField(_("note"), null=True, blank=True)

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

    def __str__(self):
        return f"Collection:{self.uuid}@{self.owner_id}:{self.title}"

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

        if self.is_dynamic:
            # Dynamic collections: use existing search-based pagination
            q = self.get_query(viewer, page=page_number)
            members = []
            pages = 0
            if q:
                r = JournalIndex.instance().search(q)
                items = r.items
                pages = r.pages
                Rating.attach_to_items(items)
                if viewer:
                    Mark.attach_to_items(viewer, items, viewer.user)
                members = [{"item": i, "parent": self} for i in items]
                if comment_as_note:
                    # Attach comments from owner as collection note if viewer is not owner
                    q = q_owned_piece_visible_to_user(
                        viewer.user if viewer else None, self.owner
                    )
                    comments = {
                        c.item.pk: c.text
                        for c in Comment.objects.filter(item__in=items).filter(q)
                    }
                    for m in members:
                        m["note"] = comments.get(m["item"].pk, "")
        else:
            all_members = self.ordered_members
            p = Paginator(all_members, page_size)
            members = p.get_page(page_number)
            pages = p.num_pages
        return members, pages

    def get_stats(self, viewer: APIdentity):
        items = self.item_ids
        stats = {"total": len(items)}
        for st, shelf in viewer.shelf_manager.shelf_list.items():
            stats[st] = shelf.members.all().filter(item_id__in=items).count()
        stats["percentage"] = (
            round(stats["complete"] * 100 / stats["total"]) if stats["total"] else 0
        )
        return stats

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
        )
        if post and post != existing_post:
            self.link_post_id(post.pk)
        return post

    @property
    def ap_object(self):
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
        }

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
