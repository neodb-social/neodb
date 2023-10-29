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
from mastodon.api import boost_toot
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
            # mark not saved yet, return default visibility for editing ui
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

    def update(
        self,
        shelf_type,
        comment_text,
        rating_grade,
        visibility,
        metadata=None,
        created_time=None,
        share_to_mastodon=False,
    ):
        post_to_feed = shelf_type is not None and (
            shelf_type != self.shelf_type
            or comment_text != self.comment_text
            or rating_grade != self.rating_grade
            or visibility != self.visibility
        )
        if shelf_type is None or visibility != self.visibility:
            if self.shelfmember:
                Takahe.delete_posts(self.shelfmember.all_post_ids)
        if created_time and created_time >= timezone.now():
            created_time = None
        post_as_new = shelf_type != self.shelf_type or visibility != self.visibility
        original_visibility = self.visibility
        if shelf_type != self.shelf_type or visibility != original_visibility:
            self.shelfmember = self.owner.shelf_manager.move_item(
                self.item,
                shelf_type,
                visibility=visibility,
                metadata=metadata,
            )
        if self.shelfmember and created_time:
            # if it's an update(not delete) and created_time is specified,
            # update the timestamp of the shelfmember and log
            log = ShelfLogEntry.objects.filter(
                owner=self.owner,
                item=self.item,
                timestamp=self.shelfmember.created_time,
            ).first()
            self.shelfmember.created_time = created_time
            self.shelfmember.save(update_fields=["created_time"])
            if log:
                log.timestamp = created_time
                log.save(update_fields=["timestamp"])
            else:
                ShelfLogEntry.objects.create(
                    owner=self.owner,
                    shelf_type=shelf_type,
                    item=self.item,
                    metadata=self.metadata,
                    timestamp=created_time,
                )
        if comment_text != self.comment_text or visibility != original_visibility:
            self.comment = Comment.comment_item(
                self.item,
                self.owner,
                comment_text,
                visibility,
                self.shelfmember.created_time if self.shelfmember else None,
            )
        if rating_grade != self.rating_grade or visibility != original_visibility:
            Rating.update_item_rating(self.item, self.owner, rating_grade, visibility)
            self.rating_grade = rating_grade

        post = Takahe.post_mark(self, post_as_new) if post_to_feed else None
        if share_to_mastodon and post:
            if (
                self.owner.user
                and self.owner.user.mastodon_token
                and self.owner.user.mastodon_site
            ):
                # TODO: make this a async task, given post to mastodon is slow and takahe post fanout may take time
                if boost_toot(
                    self.owner.user.mastodon_site,
                    self.owner.user.mastodon_token,
                    post.url,
                ):
                    return True
            return False
        else:
            return True

    def delete(self):
        # self.logs.delete()  # When deleting a mark, all logs of the mark are deleted first.
        self.update(None, None, None, 0)

    def delete_log(self, log_id):
        ShelfLogEntry.objects.filter(
            owner=self.owner, item=self.item, id=log_id
        ).delete()

    def delete_all_logs(self):
        self.logs.delete()

    @property
    def logs(self):
        return ShelfLogEntry.objects.filter(owner=self.owner, item=self.item).order_by(
            "timestamp"
        )
