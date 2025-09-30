from datetime import datetime
from functools import cached_property
from typing import Any, Iterable, Sequence

from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from catalog.models import Item
from journal.models.common import (
    q_owned_parent_piece_visible_to_user,
    q_owned_piece_visible_to_user,
)
from journal.models.tag import TagMember
from takahe.utils import Takahe
from users.models import APIdentity
from users.models.user import User

from .comment import Comment
from .note import Note
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
    def shelfmember(self) -> ShelfMember | None:
        return self.owner.shelf_manager.locate_item(self.item)

    @property
    def id(self) -> int | None:
        return self.shelfmember.pk if self.shelfmember else None

    @property
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
    def status_label(self) -> str:
        if self.shelfmember and self.shelf_type:
            return ShelfManager.get_status_label(self.shelf_type, self.item.category)
        if self.comment:
            return ShelfManager.get_status_label(
                ShelfType.PROGRESS, self.comment.item.category
            )
        return ""

    @property
    def action_label_for_feed(self) -> str:
        return str(self.action_label)

    def get_action_for_feed(self, item_link: str | None = None):
        if self.shelfmember and self.shelf_type:
            tpl = ShelfManager.get_action_template(self.shelf_type, self.item.category)
        elif self.comment:
            tpl = ShelfManager.get_action_template(
                ShelfType.PROGRESS, self.comment.item.category
            )
        else:
            tpl = ""
        if item_link:
            i = f'<a href="{item_link}">{self.item.display_title}</a>'
        else:
            i = self.item.display_title
        return _(tpl).format(item=i)

    @property
    def shelf_label(self) -> str | None:
        return (
            ShelfManager.get_label(self.shelf_type, self.item.category)
            if self.shelf_type
            else None
        )

    @cached_property
    def notes(self) -> Iterable[Note]:
        return Note.objects.filter(owner=self.owner, item=self.item).order_by(
            "-created_time"
        )
        # post_ids = PiecePost.objects.filter(
        #     piece__note__owner_id=self.owner.pk, piece__note__item_id=self.item.pk
        # ).values_list("post_id", flat=True)
        # return Takahe.get_posts(list(post_ids))

    @property
    def created_time(self) -> datetime | None:
        return self.shelfmember.created_time if self.shelfmember else None

    @property
    def metadata(self) -> dict[str, Any] | None:
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
    def tag_text(self) -> str:
        tags = [f"#{t}" for t in self.tags]
        appending = self.owner.user.preference.mastodon_append_tag
        if appending:
            tags.append(appending)
        tag_text = f"\n{' '.join(tags)}\n" if tags else ""
        return tag_text

    @cached_property
    def rating(self):
        return Rating.objects.filter(owner=self.owner, item=self.item).first()

    @property
    def rating_grade(self) -> int | None:
        return self.rating.grade if self.rating else None

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

    @classmethod
    def get_marks_by_items(
        cls, owner: APIdentity, items: Iterable[Item], viewing_user: User | None
    ) -> dict[int, "Mark"]:
        marks = {}
        for i in items:
            m = Mark(owner, i)
            m.rating = None
            m.shelfmember = None
            m.comment = None
            m.review = None
            m.tags = []
            m.notes = []
            marks[i.pk] = m
        q = q_owned_piece_visible_to_user(viewing_user, owner)
        q2 = q_owned_parent_piece_visible_to_user(viewing_user, owner)
        for m in ShelfMember.objects.filter(item__in=items).filter(q):
            marks[m.item.pk].shelfmember = m
        for c in Comment.objects.filter(item__in=items).filter(q):
            marks[c.item.pk].comment = c
        for g in Rating.objects.filter(item__in=items).filter(q):
            marks[g.item.pk].rating = g
        for r in Review.objects.filter(item__in=items).filter(q):
            marks[r.item.pk].review = r
        for n in Review.objects.filter(item__in=items).filter(q):
            marks[n.item.pk].notes.append(n)
        for t in (
            TagMember.objects.filter(item__in=items)
            .filter(q2)
            .annotate(title=F("parent__title"))
        ):
            marks[t.item.pk].tags.append(t.title)
        return marks

    @classmethod
    def attach_to_items(
        cls, owner: APIdentity, items: Sequence[Item], viewing_user: User | None
    ) -> Sequence[Item]:
        marks = Mark.get_marks_by_items(owner, items, viewing_user)
        for i in items:
            i.mark = marks.get(i.pk) or Mark(owner, i)
        return items

    @property
    def logs(self):
        return ShelfLogEntry.objects.filter(owner=self.owner, item=self.item).order_by(
            "timestamp"
        )

    """
    log entries
    log entry will be created when item is added to shelf
    log entry will be created when item is moved to another shelf
    log entry will be created when item is removed from shelf
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
        tags: list[str] | None = None,
        visibility: int | None = None,
        metadata: dict[str, Any] | None = None,
        created_time: datetime | None = None,
        share_to_mastodon: bool = False,
        application_id: int | None = None,
    ):
        """change shelf, comment or rating"""
        if created_time and created_time >= timezone.now():
            created_time = None
        if visibility is None:
            visibility = self.visibility
        last_shelf_type = self.shelf_type
        last_visibility = self.visibility if last_shelf_type else None
        update_mode = 0
        if tags is not None:
            self.owner.tag_manager.tag_item(self.item, tags, visibility)
        if shelf_type is None:
            # take item off shelf
            if self.shelfmember:
                self.shelfmember.delete_from_timeline()
                self.shelfmember.log_and_delete()
            if self.comment:
                self.comment.delete()
            if self.rating:
                self.rating.delete()
            return
        # create/update shelf member and shelf log if necessary
        if self.shelfmember and last_shelf_type == shelf_type:
            shelfmember_changed = False
            log_entry = self.shelfmember.ensure_log_entry()
            if metadata is not None and metadata != self.shelfmember.metadata:
                self.shelfmember.metadata = metadata
                shelfmember_changed = True
            if last_visibility != visibility:
                self.shelfmember.visibility = visibility
                shelfmember_changed = True
                # retract most recent post about this status when visibility changed
                update_mode = 1
            if created_time and created_time != self.shelfmember.created_time:
                self.shelfmember.created_time = created_time
                log_entry.timestamp = created_time
                try:
                    log_entry.save(update_fields=["timestamp"])
                except Exception:
                    dup_log = (
                        ShelfLogEntry.objects.filter(
                            owner_id=log_entry.owner_id,
                            shelf_type=log_entry.shelf_type,
                            item_id=log_entry.item_id,
                            timestamp=log_entry.timestamp,
                        )
                        .exclude(pk=log_entry.pk)
                        .first()
                    )
                    if dup_log:
                        log_entry.delete()
                        log_entry = dup_log
                shelfmember_changed = True
            if shelfmember_changed:
                self.shelfmember.save()
        else:
            # ignore most recent post if exists and create new one
            shelfmember_changed = True
            update_mode = 2
            shelf = self.owner.shelf_manager.get_shelf(shelf_type)
            d = {"parent": shelf, "visibility": visibility, "position": 0}
            if metadata:
                d["metadata"] = metadata
            d["created_time"] = created_time or timezone.now()
            self.shelfmember, _ = ShelfMember.objects.update_or_create(
                owner=self.owner, item=self.item, defaults=d
            )
            log_entry = self.shelfmember.ensure_log_entry()
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
                if self.comment and not shelfmember_changed:
                    self.shelfmember.edited_time = self.comment.edited_time
                    self.shelfmember.save(update_fields=["edited_time"])
        # create/update/detele rating if necessary
        if rating_grade is not None:
            if rating_grade != self.rating_grade or visibility != last_visibility:
                self.rating = Rating.update_item_rating(
                    self.item, self.owner, rating_grade, visibility
                )
        # store changed rating/comment if needed
        if (
            log_entry.rating_grade != self.rating_grade
            or log_entry.comment_text != self.comment_text
        ):
            log_entry.rating_grade = self.rating_grade
            log_entry.comment_text = self.comment_text
            log_entry.save(update_fields=["metadata"])
        # publish a new or updated ActivityPub post
        post = self.shelfmember.sync_to_timeline(
            update_mode, application_id=application_id
        )
        if share_to_mastodon:
            self.shelfmember.sync_to_social_accounts(update_mode)
        self.shelfmember.update_index()
        # auto add bookmark
        if (
            post
            and shelf_type == ShelfType.PROGRESS
            and self.item.category
            in (self.owner.user.preference.auto_bookmark_cats or [])
        ):
            Takahe.bookmark(post.pk, self.owner.pk)

    def delete(self, keep_tags=False):
        self.update(None, tags=None if keep_tags else [])

    def delete_log(self, log_id: int):
        ShelfLogEntry.objects.filter(
            owner=self.owner, item=self.item, id=log_id
        ).delete()

    def delete_all_logs(self):
        self.logs.delete()

    @staticmethod
    def get_mark_count_for_item(item: Item) -> int:
        if item.get_type() in ["Podcast", "TVSeason"]:
            return (
                ShelfMember.objects.filter(item_id__in=item.child_item_ids + [item.pk])
                .values("owner_id")
                .distinct()
                .count()
            )
        else:
            return ShelfMember.objects.filter(item=item).count()
