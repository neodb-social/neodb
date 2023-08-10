from functools import cached_property

import django.dispatch
from django.db import models
from django.utils import timezone

from catalog.models import Item, ItemCategory
from users.models import User

from .common import Piece

list_add = django.dispatch.Signal()
list_remove = django.dispatch.Signal()


class List(Piece):
    """
    List (abstract class)
    """

    owner = models.ForeignKey(User, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(
        default=0
    )  # 0: Public / 1: Follower only / 2: Self only
    created_time = models.DateTimeField(
        default=timezone.now
    )  # auto_now_add=True  FIXME revert this after migration
    edited_time = models.DateTimeField(
        default=timezone.now
    )  # auto_now=True   FIXME revert this after migration
    metadata = models.JSONField(default=dict)

    class Meta:
        abstract = True

    # MEMBER_CLASS = None  # subclass must override this
    # subclass must add this:
    # items = models.ManyToManyField(Item, through='ListMember')

    @property
    def ordered_members(self):
        return self.members.all().order_by("position")

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

    def get_summary(self):
        summary = {k: 0 for k in ItemCategory.values}
        for c in self.recent_items:
            summary[c.category] += 1
        return summary

    def append_item(self, item, **params):
        """
        named metadata fields should be specified directly, not in metadata dict!
        e.g. collection.append_item(item, note="abc") works, but collection.append_item(item, metadata={"note":"abc"}) doesn't
        """
        if item is None:
            return None
        member = self.get_member_for_item(item)
        if member:
            return member
        ml = self.ordered_members
        p = {"parent": self}
        p.update(params)
        member = self.MEMBER_CLASS.objects.create(
            owner=self.owner,
            position=ml.last().position + 1 if ml.count() else 1,
            item=item,
            **p,
        )
        list_add.send(sender=self.__class__, instance=self, item=item, member=member)
        return member

    def remove_item(self, item):
        member = self.get_member_for_item(item)
        if member:
            list_remove.send(
                sender=self.__class__, instance=self, item=item, member=member
            )
            member.delete()

    def update_member_order(self, ordered_member_ids):
        members = self.ordered_members
        for m in self.members.all():
            try:
                i = ordered_member_ids.index(m.id)
                if m.position != i + 1:
                    m.position = i + 1
                    m.save()
            except ValueError:
                pass

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

    def update_item_metadata(self, item, metadata):
        member = self.get_member_for_item(item)
        if member:
            member.metadata = metadata
            member.save()


class ListMember(Piece):
    """
    ListMember - List class's member class
    It's an abstract class, subclass must add this:

    parent = models.ForeignKey('List', related_name='members', on_delete=models.CASCADE)
    """

    owner = models.ForeignKey(User, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(
        default=0
    )  # 0: Public / 1: Follower only / 2: Self only
    created_time = models.DateTimeField(default=timezone.now)
    edited_time = models.DateTimeField(
        default=timezone.now
    )  # auto_now=True   FIXME revert this after migration
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
        return f"{self.id}:{self.position} ({self.item})"
