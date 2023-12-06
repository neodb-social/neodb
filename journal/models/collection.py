import re
from functools import cached_property

from django.db import models
from django.utils.translation import gettext_lazy as _

from catalog.collection.models import Collection as CatalogCollection
from catalog.common import jsondata
from catalog.common.utils import DEFAULT_ITEM_COVER, piece_cover_path
from catalog.models import Item
from users.models import APIdentity

from .common import Piece
from .itemlist import List, ListMember
from .renderers import render_md

_RE_HTML_TAG = re.compile(r"<[^>]*>")


class CollectionMember(ListMember):
    parent = models.ForeignKey(
        "Collection", related_name="members", on_delete=models.CASCADE
    )

    note = jsondata.CharField(_("备注"), null=True, blank=True)


class Collection(List):
    url_path = "collection"
    MEMBER_CLASS = CollectionMember
    catalog_item = models.OneToOneField(
        CatalogCollection, on_delete=models.PROTECT, related_name="journal_item"
    )
    title = models.CharField(_("标题"), max_length=1000, default="")
    brief = models.TextField(_("简介"), blank=True, default="")
    cover = models.ImageField(
        upload_to=piece_cover_path, default=DEFAULT_ITEM_COVER, blank=True
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

    @property
    def html(self):
        html = render_md(self.brief)
        return html

    @property
    def plain_description(self):
        html = render_md(self.brief)
        return _RE_HTML_TAG.sub(" ", html)

    def featured_since(self, owner: APIdentity):
        f = FeaturedCollection.objects.filter(target=self, owner=owner).first()
        return f.created_time if f else None

    def get_stats(self, owner: APIdentity):
        items = list(self.members.all().values_list("item_id", flat=True))
        stats = {"total": len(items)}
        for st, shelf in owner.shelf_manager.shelf_list.items():
            stats[st] = shelf.members.all().filter(item_id__in=items).count()
        stats["percentage"] = (
            round(stats["complete"] * 100 / stats["total"]) if stats["total"] else 0
        )
        return stats

    def get_progress(self, owner: APIdentity):
        items = list(self.members.all().values_list("item_id", flat=True))
        if len(items) == 0:
            return 0
        shelf = owner.shelf_manager.shelf_list["complete"]
        return round(
            shelf.members.all().filter(item_id__in=items).count() * 100 / len(items)
        )

    def save(self, *args, **kwargs):
        from takahe.utils import Takahe

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
        Takahe.post_collection(self)

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
