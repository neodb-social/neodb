import re
import uuid
from functools import cached_property

import django.dispatch
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import connection, models
from django.db.models import Avg, Count, Q
from django.utils import timezone
from django.utils.baseconv import base62
from django.utils.translation import gettext_lazy as _
from loguru import logger
from markdownx.models import MarkdownxField
from polymorphic.models import PolymorphicModel

from catalog.collection.models import Collection as CatalogCollection
from catalog.common import jsondata
from catalog.common.models import Item, ItemCategory
from catalog.common.utils import DEFAULT_ITEM_COVER, piece_cover_path
from mastodon.api import boost_toot_later, share_mark
from takahe.utils import Takahe
from users.models import APIdentity

from .comment import Comment
from .rating import Rating
from .review import Review
from .shelf import Shelf, ShelfLogEntry, ShelfManager, ShelfMember, ShelfType


class Mark:
    """
    Holding Mark for an item on an shelf,
    which is a combo object of ShelfMember, Comment, Rating and Tags.
    it mimics previous mark behaviour.
    """

    def __init__(self, owner: APIdentity, item: Item):
        self.owner = owner
        self.item = item

    @cached_property
    def shelfmember(self) -> ShelfMember:
        return self.owner.shelf_manager.locate_item(self.item)

    @property
    def id(self) -> int | None:
        return self.shelfmember.id if self.shelfmember else None

    @cached_property
    def shelf(self) -> Shelf | None:
        return self.shelfmember.parent if self.shelfmember else None

    @property
    def shelf_type(self) -> ShelfType | None:
        return self.shelfmember.parent.shelf_type if self.shelfmember else None

    @property
    def action_label(self) -> str:
        if self.shelfmember and self.shelf_type:
            return ShelfManager.get_action_label(self.shelf_type, self.item.category)
        if self.comment:
            return ShelfManager.get_action_label(
                ShelfType.PROGRESS, self.comment.item.category
            )
        return ""

    @property
    def action_label_for_feed(self) -> str:
        return re.sub(r"不(.+)了", r"不再\1", str(self.action_label))  # TODO i18n

    @property
    def shelf_label(self) -> str | None:
        return (
            ShelfManager.get_label(self.shelf_type, self.item.category)
            if self.shelf_type
            else None
        )

    @property
    def created_time(self):
        return self.shelfmember.created_time if self.shelfmember else None

    @property
    def metadata(self) -> dict | None:
        return self.shelfmember.metadata if self.shelfmember else None

    @property
    def visibility(self) -> int:
        if self.shelfmember:
            return self.shelfmember.visibility
        else:
            # mark not created/saved yet, use user's default visibility
            return self.owner.preference.default_visibility

    @cached_property
    def tags(self) -> list[str]:
        return self.owner.tag_manager.get_item_tags(self.item)

    @cached_property
    def rating(self):
        return Rating.objects.filter(owner=self.owner, item=self.item).first()

    @cached_property
    def rating_grade(self) -> int | None:
        return Rating.get_item_rating(self.item, self.owner)

    @cached_property
    def comment(self) -> Comment | None:
        return Comment.objects.filter(owner=self.owner, item=self.item).first()

    @property
    def comment_text(self) -> str | None:
        return (self.comment.text or None) if self.comment else None

    @property
    def comment_html(self) -> str | None:
        return self.comment.html if self.comment else None

    @cached_property
    def review(self) -> Review | None:
        return Review.objects.filter(owner=self.owner, item=self.item).first()

    @property
    def logs(self):
        return ShelfLogEntry.objects.filter(owner=self.owner, item=self.item).order_by(
            "timestamp"
        )

    """
    log entries
    log entry will be created when item is added to shelf
    log entry will be created when item is moved to another shelf
    log entry will be created when item is removed from shelf (TODO change this to DEFERRED shelf)
    timestamp of log entry will be updated whenever created_time of shelfmember is updated
    any log entry can be deleted by user arbitrarily

    posts
    post will be created and set as current when item added to shelf
    current post will be updated when comment or rating is updated
    post will not be updated if only created_time is changed
    post will be deleted, re-created and set as current if visibility changed
    when item is moved to another shelf, a new post will be created
    when item is removed from shelf, all post will be deleted

    boost
    post will be boosted to mastodon if user has mastodon token and site configured
    """

    @property
    def all_post_ids(self):
        """all post ids for this user and item"""
        return self.logs.values_list("posts", flat=True)

    @property
    def current_post_ids(self):
        """all post ids for this user and item for its current status"""
        return self.shelfmember.all_post_ids if self.shelfmember else []

    @property
    def latest_post_id(self):
        """latest post id for this user and item for its current status"""
        return self.shelfmember.latest_post_id if self.shelfmember else None

    def update(
        self,
        shelf_type: ShelfType | None,
        comment_text: str | None = None,
        rating_grade: int | None = None,
        visibility: int | None = None,
        metadata=None,
        created_time=None,
        share_to_mastodon=False,
    ):
        """change shelf, comment or rating"""
        if created_time and created_time >= timezone.now():
            created_time = None
        if visibility is None:
            visibility = self.visibility
        last_shelf_type = self.shelf_type
        last_visibility = self.visibility if last_shelf_type else None
        if shelf_type is None:  # TODO change this use case to DEFERRED status
            # take item off shelf
            if last_shelf_type:
                Takahe.delete_posts(self.shelfmember.all_post_ids)
                self.shelfmember.log_and_delete()
            if self.comment:
                self.comment.delete()
            if self.rating:
                self.rating.delete()
            return
        # create/update shelf member and shelf log if necessary
        if last_shelf_type == shelf_type:
            shelfmember_changed = False
            log_entry = self.shelfmember.ensure_log_entry()
            if metadata is not None and metadata != self.shelfmember.metadata:
                self.shelfmember.metadata = metadata
                shelfmember_changed = True
            if last_visibility != visibility:
                self.shelfmember.visibility = visibility
                shelfmember_changed = True
                # retract most recent post about this status when visibility changed
                if self.shelfmember.latest_post:
                    Takahe.delete_posts([self.shelfmember.latest_post.pk])
            if created_time and created_time != self.shelfmember.created_time:
                self.shelfmember.created_time = created_time
                log_entry.timestamp = created_time
                try:
                    log_entry.save(update_fields=["timestamp"])
                except:
                    log_entry.delete()
                shelfmember_changed = True
            if shelfmember_changed:
                self.shelfmember.save()
        else:
            shelf = Shelf.objects.get(owner=self.owner, shelf_type=shelf_type)
            d = {"parent": shelf, "visibility": visibility, "position": 0}
            if metadata:
                d["metadata"] = metadata
            d["created_time"] = created_time or timezone.now()
            self.shelfmember, _ = ShelfMember.objects.update_or_create(
                owner=self.owner, item=self.item, defaults=d
            )
            self.shelfmember.ensure_log_entry()
            self.shelfmember.clear_post_ids()
        # create/update/detele comment if necessary
        if comment_text is not None:
            if comment_text != self.comment_text or visibility != last_visibility:
                self.comment = Comment.comment_item(
                    self.item,
                    self.owner,
                    comment_text,
                    visibility,
                    self.shelfmember.created_time,
                )
        # create/update/detele rating if necessary
        if rating_grade is not None:
            if rating_grade != self.rating_grade or visibility != last_visibility:
                Rating.update_item_rating(
                    self.item, self.owner, rating_grade, visibility
                )
                self.rating_grade = rating_grade
        # publish a new or updated ActivityPub post
        post_as_new = shelf_type != last_shelf_type or visibility != last_visibility
        classic_repost = self.owner.user.preference.mastodon_repost_mode == 1
        append = (
            f"@{self.owner.user.mastodon_acct}\n"
            if visibility > 0 and share_to_mastodon and not classic_repost
            else ""
        )
        post = Takahe.post_mark(self, post_as_new, append)
        # async boost to mastodon
        if post and share_to_mastodon:
            if classic_repost:
                share_mark(self, post_as_new)
            else:
                boost_toot_later(self.owner.user, post.url)
        return True

    def delete(self):
        # self.logs.delete()  # When deleting a mark, all logs of the mark are deleted first.
        self.update(None)

    def delete_log(self, log_id):
        ShelfLogEntry.objects.filter(
            owner=self.owner, item=self.item, id=log_id
        ).delete()

    def delete_all_logs(self):
        self.logs.delete()
