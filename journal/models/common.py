import re
import uuid
from abc import abstractmethod
from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING, Any, Self

# from deepmerge import always_merger
from django.conf import settings
from django.core.signing import b62_decode, b62_encode
from django.db import connection, models
from django.db.models import Avg, CharField, Count, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from loguru import logger
from polymorphic.models import PolymorphicModel

from catalog.common.models import AvailableItemCategory, Item, ItemCategory
from catalog.models import item_categories, item_content_types
from mastodon.api import boost_toot_later, delete_toot, delete_toot_later, post_toot2
from takahe.utils import Takahe
from users.models import APIdentity, User

from .mixins import UserOwnedObjectMixin

if TYPE_CHECKING:
    from takahe.models import Post

    from .like import Like


class VisibilityType(models.IntegerChoices):
    Public = 0, _("Public")  # type:ignore[reportCallIssue]
    Follower_Only = 1, _("Followers Only")  # type:ignore[reportCallIssue]
    Private = 2, _("Mentioned Only")  # type:ignore[reportCallIssue]


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


def q_item_in_category(item_category: ItemCategory):
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
    if TYPE_CHECKING:
        likes: models.QuerySet["Like"]
    url_path = "p"  # subclass must specify this
    uid = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    local = models.BooleanField(default=True)
    posts = models.ManyToManyField(
        "takahe.Post", related_name="pieces", through="PiecePost"
    )

    def get_mastodon_repost_url(self):
        return (self.metadata or {}).get("shared_link")

    def set_mastodon_repost_url(self, url: str | None):
        metadata = self.metadata or {}
        if metadata.get("shared_link", None) == url:
            return
        if not url:
            metadata.pop("shared_link", None)
        else:
            metadata["shared_link"] = url
        self.metadata = metadata
        self.save(update_fields=["metadata"])

    def delete(self, *args, **kwargs):
        if self.local:
            Takahe.delete_posts(self.all_post_ids)
            toot_url = self.get_mastodon_repost_url()
            if toot_url:
                delete_toot_later(self.owner.user, toot_url)
        return super().delete(*args, **kwargs)

    @property
    def uuid(self):
        return b62_encode(self.uid.int)

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
            obj = cls.objects.get(uid=uuid.UUID(int=b62_decode(b62)))
        except Exception:
            obj = None
        return obj

    @classmethod
    def get_by_post_id(cls, post_id: int):
        pp = PiecePost.objects.filter(post_id=post_id).first()
        return pp.piece if pp else None

    def link_post_id(self, post_id: int):
        PiecePost.objects.get_or_create(piece=self, post_id=post_id)
        try:
            del self.latest_post_id
            del self.latest_post
        except AttributeError:
            pass

    def clear_post_ids(self):
        PiecePost.objects.filter(piece=self).delete()

    @cached_property
    def latest_post_id(self):
        # post id is ordered by their created time
        pp = PiecePost.objects.filter(piece=self).order_by("-post_id").first()
        return pp.post_id if pp else None

    @cached_property
    def latest_post(self) -> "Post | None":
        pk = self.latest_post_id
        return Takahe.get_post(pk) if pk else None

    @cached_property
    def all_post_ids(self):
        post_ids = list(
            PiecePost.objects.filter(piece=self).values_list("post_id", flat=True)
        )
        return post_ids

    @property
    def ap_object(self):
        raise NotImplementedError("subclass must implement this")

    @classmethod
    @abstractmethod
    def params_from_ap_object(cls, post, obj, piece):
        return {}

    @abstractmethod
    def to_post_params(self):
        return {}

    @abstractmethod
    def to_mastodon_params(self):
        return {}

    @classmethod
    def update_by_ap_object(cls, owner: APIdentity, item: Item, obj, post: "Post"):
        """
        Create or update a content piece with related AP message
        """
        p = cls.get_by_post_id(post.id)
        if p and p.owner.pk != post.author_id:
            logger.warning(f"Owner mismatch: {p.owner.pk} != {post.author_id}")
            return
        local = post.local
        visibility = Takahe.visibility_t2n(post.visibility)
        d = cls.params_from_ap_object(post, obj, p)
        if p:
            # update existing piece
            edited = post.edited if local else datetime.fromisoformat(obj["updated"])
            if p.edited_time >= edited:
                # incoming ap object is older than what we have, no update needed
                return p
            d["edited_time"] = edited
            for k, v in d.items():
                setattr(p, k, v)
            p.save(update_fields=d.keys())
        else:
            # no previously linked piece, create a new one and link to post
            d.update(
                {
                    "item": item,
                    "owner": owner,
                    "local": post.local,
                    "visibility": visibility,
                    "remote_id": None if local else obj["id"],
                }
            )
            if local:
                d["created_time"] = post.published
                d["edited_time"] = post.edited or post.published
            else:
                d["created_time"] = datetime.fromisoformat(obj["published"])
                d["edited_time"] = datetime.fromisoformat(obj["updated"])
            p = cls.objects.create(**d)
            p.link_post_id(post.id)
            if local:
                # a local piece is reconstructred from a post, update post and fanout
                if not post.type_data:
                    post.type_data = {}
                # always_merger.merge(
                #     post.type_data,
                #     {
                #         "object": {
                #             "tag": [item.ap_object_ref],
                #             "relatedWith": [p.ap_object],
                #         }
                #     },
                # )
                post.type_data = {
                    "object": {
                        "tag": [item.ap_object_ref],
                        "relatedWith": [p.ap_object],
                    }
                }
                post.save(update_fields=["type_data"])
                Takahe.update_state(post, "edited")
        return p

    def sync_to_mastodon(self, delete_existing=False):
        user = self.owner.user
        if not user.mastodon_site:
            return
        if user.preference.mastodon_repost_mode == 1:
            if delete_existing:
                self.delete_mastodon_repost()
            return self.repost_to_mastodon()
        elif self.latest_post:
            return boost_toot_later(user, self.latest_post.url)
        else:
            logger.warning("No post found for piece")
            return False, 404

    def delete_mastodon_repost(self):
        toot_url = self.get_mastodon_repost_url()
        if toot_url:
            self.set_mastodon_repost_url(None)
            delete_toot(self.owner.user, toot_url)

    def repost_to_mastodon(self):
        user = self.owner.user
        d = {
            "user": user,
            "visibility": self.visibility,
            "update_toot_url": self.get_mastodon_repost_url(),
        }
        d.update(self.to_mastodon_params())
        response = post_toot2(**d)
        if response is not None and response.status_code in [200, 201]:
            j = response.json()
            if "url" in j:
                metadata = {"shared_link": j["url"]}
                if self.metadata != metadata:
                    self.metadata = metadata
                    self.save(update_fields=["metadata"])
            return True, 200
        else:
            logger.warning(response)
            return False, response.status_code if response is not None else -1

    def sync_to_timeline(self, delete_existing=False):
        user = self.owner.user
        v = Takahe.visibility_n2t(self.visibility, user.preference.post_public_mode)
        existing_post = self.latest_post
        if existing_post and existing_post.state in ["deleted", "deleted_fanned_out"]:
            existing_post = None
        elif existing_post and delete_existing:
            Takahe.delete_posts([existing_post.pk])
            existing_post = None
        params = {
            "author_pk": self.owner.pk,
            "visibility": v,
            "post_pk": existing_post.pk if existing_post else None,
            "post_time": self.created_time,  # type:ignore subclass must have this
            "edit_time": self.edited_time,  # type:ignore subclass must have this
            "data": {
                "object": {
                    "tag": (
                        [self.item.ap_object_ref]  # type:ignore
                        if hasattr(self, "item")
                        else []
                    ),
                    "relatedWith": [self.ap_object],
                }
            },
        }
        params.update(self.to_post_params())
        post = Takahe.post(**params)
        if post and post != existing_post:
            self.link_post_id(post.pk)
        return post


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
        choices=VisibilityType.choices, default=0, null=False
    )  # type:ignore
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
    def create_from_piece(cls, c: Content):
        return cls.objects.create(
            class_name=c.__class__.__name__,
            owner=c.owner,
            visibility=c.visibility,
            created_time=c.created_time,
            metadata=c.ap_object,
            item=c.item,
            remote_id=c.remote_id if hasattr(c, "remote_id") else None,
        )
