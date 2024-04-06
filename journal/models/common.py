import re
import uuid
from functools import cached_property
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import connection, models
from django.db.models import Avg, CharField, Count, Q
from django.utils import timezone
from django.utils.baseconv import base62
from django.utils.translation import gettext_lazy as _
from polymorphic.models import PolymorphicModel

from catalog.common.models import AvailableItemCategory, Item, ItemCategory
from catalog.models import item_categories, item_content_types
from takahe.utils import Takahe
from users.models import APIdentity, User

from .mixins import UserOwnedObjectMixin

if TYPE_CHECKING:
    from takahe.models import Post


class VisibilityType(models.IntegerChoices):
    Public = 0, _("Public")
    Follower_Only = 1, _("Followers Only")
    Private = 2, _("Mentioned Only")


def q_owned_piece_visible_to_user(viewing_user: User, owner: APIdentity):
    if not viewing_user or not viewing_user.is_authenticated:
        if owner.anonymous_viewable:
            return Q(owner=owner, visibility=0)
        else:
            return Q(pk__in=[])
    viewer = viewing_user.identity
    if viewer == owner:
        return Q(owner=owner)
    # elif viewer.is_blocked_by(owner):
    #     return Q(pk__in=[])
    elif viewer.is_following(owner):
        return Q(owner=owner, visibility__in=[0, 1])
    else:
        return Q(owner=owner, visibility=0)


def max_visiblity_to_user(viewing_user: User, owner: APIdentity):
    if not viewing_user or not viewing_user.is_authenticated:
        return 0
    viewer = viewing_user.identity
    if viewer == owner:
        return 2
    elif viewer.is_following(owner):
        return 1
    else:
        return 0


def q_piece_visible_to_user(viewing_user: User):
    if not viewing_user or not viewing_user.is_authenticated:
        return Q(visibility=0, owner__anonymous_viewable=True)
    viewer = viewing_user.identity
    return (
        Q(visibility=0)
        | Q(owner_id__in=viewer.following, visibility=1)
        | Q(owner_id=viewer.pk)
    ) & ~Q(owner_id__in=viewer.ignoring)


def q_piece_in_home_feed_of_user(viewing_user: User):
    viewer = viewing_user.identity
    return Q(owner_id__in=viewer.following, visibility__lt=2) | Q(owner_id=viewer.pk)


def q_item_in_category(item_category: ItemCategory | AvailableItemCategory):
    classes = item_categories()[item_category]
    # q = Q(item__instance_of=classes[0])
    # for cls in classes[1:]:
    #     q = q | Q(instance_of=cls)
    # return q
    ct = item_content_types()
    contenttype_ids = [ct[cls] for cls in classes]
    return Q(item__polymorphic_ctype__in=contenttype_ids)


# class ImportStatus(Enum):
#     QUEUED = 0
#     PROCESSING = 1
#     FINISHED = 2


# class ImportSession(models.Model):
#     owner = models.ForeignKey(APIdentity, on_delete=models.CASCADE)
#     status = models.PositiveSmallIntegerField(default=ImportStatus.QUEUED)
#     importer = models.CharField(max_length=50)
#     file = models.CharField()
#     default_visibility = models.PositiveSmallIntegerField()
#     total = models.PositiveIntegerField()
#     processed = models.PositiveIntegerField()
#     skipped = models.PositiveIntegerField()
#     imported = models.PositiveIntegerField()
#     failed = models.PositiveIntegerField()
#     logs = models.JSONField(default=list)
#     created_time = models.DateTimeField(auto_now_add=True)
#     edited_time = models.DateTimeField(auto_now=True)

#     class Meta:
#         indexes = [
#             models.Index(fields=["owner", "importer", "created_time"]),
#         ]


class Piece(PolymorphicModel, UserOwnedObjectMixin):
    url_path = "p"  # subclass must specify this
    uid = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    local = models.BooleanField(default=True)
    posts = models.ManyToManyField(
        "takahe.Post", related_name="pieces", through="PiecePost"
    )

    @property
    def uuid(self):
        return base62.encode(self.uid.int)

    @property
    def url(self):
        return f"/{self.url_path}/{self.uuid}" if self.url_path else None

    @property
    def absolute_url(self):
        return (settings.SITE_INFO["site_url"] + self.url) if self.url_path else None

    @property
    def api_url(self):
        return f"/api/{self.url}" if self.url_path else None

    @property
    def shared_link(self):
        return Takahe.get_post_url(self.latest_post.pk) if self.latest_post else None

    @property
    def like_count(self):
        return (
            Takahe.get_post_stats(self.latest_post.pk).get("likes", 0)
            if self.latest_post
            else 0
        )

    def is_liked_by(self, identity):
        return self.latest_post and Takahe.post_liked_by(
            self.latest_post.pk, identity.pk
        )

    @property
    def reply_count(self):
        return (
            Takahe.get_post_stats(self.latest_post.pk).get("replies", 0)
            if self.latest_post
            else 0
        )

    def get_replies(self, viewing_identity):
        return Takahe.get_replies_for_posts(
            self.all_post_ids, viewing_identity.pk if viewing_identity else None
        )

    @classmethod
    def get_by_url(cls, url_or_b62):
        b62 = url_or_b62.strip().split("/")[-1]
        if len(b62) not in [21, 22]:
            r = re.search(r"[A-Za-z0-9]{21,22}", url_or_b62)
            if r:
                b62 = r[0]
        try:
            obj = cls.objects.get(uid=uuid.UUID(int=base62.decode(b62)))
        except Exception:
            obj = None
        return obj

    @classmethod
    def update_by_ap_object(cls, owner, item, obj, post_id, visibility):
        raise NotImplementedError("subclass must implement this")

    @property
    def ap_object(self):
        raise NotImplementedError("subclass must implement this")

    def link_post_id(self, post_id: int):
        PiecePost.objects.get_or_create(piece=self, post_id=post_id)

    def clear_post_ids(self):
        PiecePost.objects.filter(piece=self).delete()

    @cached_property
    def latest_post_id(self):
        # post id is ordered by their created time
        pp = PiecePost.objects.filter(piece=self).order_by("-post_id").first()
        return pp.post_id if pp else None

    @cached_property
    def latest_post(self):
        pk = self.latest_post_id
        return Takahe.get_post(pk) if pk else None

    @cached_property
    def all_post_ids(self):
        post_ids = list(
            PiecePost.objects.filter(piece=self).values_list("post_id", flat=True)
        )
        return post_ids


class PiecePost(models.Model):
    post_id: int
    piece = models.ForeignKey(Piece, on_delete=models.CASCADE)
    post = models.ForeignKey(
        "takahe.Post", db_constraint=False, db_index=True, on_delete=models.CASCADE
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["piece", "post"], name="unique_piece_post"),
        ]


class PieceInteraction(models.Model):
    target = models.ForeignKey(
        Piece, on_delete=models.CASCADE, related_name="interactions"
    )
    target_type = models.CharField(max_length=50)
    interaction_type = models.CharField(max_length=50)
    identity = models.ForeignKey(
        APIdentity, on_delete=models.CASCADE, related_name="interactions"
    )
    created_time = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["identity", "interaction_type", "target"],
                name="unique_interaction",
            ),
        ]
        indexes = [
            models.Index(fields=["identity", "interaction_type", "created_time"]),
            models.Index(fields=["target", "interaction_type", "created_time"]),
        ]


class Content(Piece):
    owner = models.ForeignKey(APIdentity, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(
        default=0
    )  # 0: Public / 1: Follower only / 2: Self only
    created_time = models.DateTimeField(default=timezone.now)
    edited_time = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    remote_id = models.CharField(max_length=200, null=True, default=None)

    def __str__(self):
        return f"{self.__class__.__name__}:{self.uuid}@{self.item}"

    class Meta:
        abstract = True


class Debris(Content):
    class_name = CharField(max_length=50)

    @classmethod
    def create_from_piece(cls, c: Piece):
        return cls.objects.create(
            class_name=c.__class__.__name__,
            owner=c.owner,
            visibility=c.visibility,
            created_time=c.created_time,
            metadata=c.ap_object,
            item=c.item,
            remote_id=c.remote_id if hasattr(c, "remote_id") else None,
        )
