import re
from functools import cached_property

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import connection, models
from django.db.models import Avg, Count, Q
from django.utils.translation import gettext_lazy as _

from catalog.collection.models import Collection as CatalogCollection
from catalog.models import Item
from users.models import User

from .itemlist import List, ListMember


class TagMember(ListMember):
    parent = models.ForeignKey("Tag", related_name="members", on_delete=models.CASCADE)

    class Meta:
        unique_together = [["parent", "item"]]


TagValidators = [RegexValidator(regex=r"\s+", inverse_match=True)]


class Tag(List):
    MEMBER_CLASS = TagMember
    items = models.ManyToManyField(Item, through="TagMember")
    title = models.CharField(
        max_length=100, null=False, blank=False, validators=TagValidators
    )
    # TODO case convert and space removal on save
    # TODO check on save

    class Meta:
        unique_together = [["owner", "title"]]

    @staticmethod
    def cleanup_title(title, replace=True):
        t = re.sub(r"\s+", " ", title.strip())
        return "_" if not title and replace else t

    @staticmethod
    def deep_cleanup_title(title):
        """Remove all non-word characters, only for public index purpose"""
        return re.sub(r"\W+", " ", title).strip()


class TagManager:
    @staticmethod
    def indexable_tags_for_item(item):
        tags = (
            item.tag_set.all()
            .filter(visibility=0)
            .values("title")
            .annotate(frequency=Count("owner"))
            .order_by("-frequency")[:20]
        )
        tag_titles = sorted(
            [
                t
                for t in set(map(lambda t: Tag.deep_cleanup_title(t["title"]), tags))
                if t
            ]
        )
        return tag_titles

    @staticmethod
    def all_tags_for_user(user, public_only=False):
        tags = (
            user.tag_set.all()
            .values("title")
            .annotate(frequency=Count("members__id"))
            .order_by("-frequency")
        )
        if public_only:
            tags = tags.filter(visibility=0)
        return list(map(lambda t: t["title"], tags))

    @staticmethod
    def tag_item_by_user(item, user, tag_titles, default_visibility=0):
        titles = set([Tag.cleanup_title(tag_title) for tag_title in tag_titles])
        current_titles = set(
            [m.parent.title for m in TagMember.objects.filter(owner=user, item=item)]
        )
        for title in titles - current_titles:
            tag = Tag.objects.filter(owner=user, title=title).first()
            if not tag:
                tag = Tag.objects.create(
                    owner=user, title=title, visibility=default_visibility
                )
            tag.append_item(item, visibility=default_visibility)
        for title in current_titles - titles:
            tag = Tag.objects.filter(owner=user, title=title).first()
            if tag:
                tag.remove_item(item)

    @staticmethod
    def get_item_tags_by_user(item, user):
        current_titles = [
            m.parent.title for m in TagMember.objects.filter(owner=user, item=item)
        ]
        return current_titles

    @staticmethod
    def get_manager_for_user(user):
        return TagManager(user)

    def __init__(self, user):
        self.owner = user

    @property
    def all_tags(self):
        return TagManager.all_tags_for_user(self.owner)

    @property
    def public_tags(self):
        return TagManager.all_tags_for_user(self.owner, public_only=True)

    def get_item_tags(self, item):
        return sorted(
            [
                m["parent__title"]
                for m in TagMember.objects.filter(
                    parent__owner=self.owner, item=item
                ).values("parent__title")
            ]
        )
