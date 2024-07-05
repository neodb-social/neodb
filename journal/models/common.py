import re
import uuid
from abc import abstractmethod
from datetime import datetime
from functools import cached_property
from operator import pos
from typing import TYPE_CHECKING, Any, Self

import django_rq

# from deepmerge import always_merger
from django.conf import settings
from django.core.exceptions import PermissionDenied, RequestAborted
from django.core.signing import b62_decode, b62_encode
from django.db import models
from django.db.models import CharField, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from loguru import logger
from polymorphic.models import PolymorphicModel
from user_messages import api as messages

from catalog.common.models import Item, ItemCategory
from catalog.models import item_categories, item_content_types
from takahe.utils import Takahe
from users.middlewares import activate_language_for_user
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


class Piece(PolymorphicModel, UserOwnedObjectMixin):
    if TYPE_CHECKING:
        likes: models.QuerySet["Like"]
        metadata: models.JSONField[Any, Any]
    url_path = "p"  # subclass must specify this
    uid = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    local = models.BooleanField(default=True)
    posts = models.ManyToManyField(
        "takahe.Post", related_name="pieces", through="PiecePost"
    )

    @property
    def classname(self) -> str:
        return self.__class__.__name__.lower()

    def delete(self, *args, **kwargs):
        if self.local:
            Takahe.delete_posts(self.all_post_ids)
            self.delete_crossposts()
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
    def params_from_ap_object(
        cls, post: "Post", obj: dict[str, Any], piece: Self | None
    ) -> dict[str, Any]:
        return {}

    @abstractmethod
    def to_post_params(self) -> dict[str, Any]:
        return {}

    @abstractmethod
    def to_crosspost_params(self) -> dict[str, Any]:
        return {}

    @classmethod
    def update_by_ap_object(
        cls, owner: APIdentity, item: Item, obj, post: "Post"
    ) -> Self | None:
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
        # subclass may have to add additional code to update type_data in local post
        return p

    @classmethod
    def _delete_crossposts(cls, user_pk, metadata: dict):
        user = User.objects.get(pk=user_pk)
        toot_id = metadata.get("mastodon_id")
        if toot_id and user.mastodon:
            user.mastodon.delete_post(toot_id)
        post_id = metadata.get("bluesky_id")
        if toot_id and user.bluesky:
            user.bluesky.delete_post(post_id)

    def delete_crossposts(self):
        if hasattr(self, "metadata") and self.metadata:
            django_rq.get_queue("mastodon").enqueue(
                self._delete_crossposts, self.owner.user_id, self.metadata
            )

    def get_crosspost_params(self):
        d = {
            "visibility": self.visibility,
            "update_ids": self.metadata.copy() if hasattr(self, "metadata") else {},
        }
        d.update(self.to_crosspost_params())
        return d

    def sync_to_social_accounts(self, update_mode: int = 0):
        """update_mode: 0 update if exists otherwise create; 1: delete if exists and create; 2: only create"""
        django_rq.get_queue("mastodon").enqueue(
            self._sync_to_social_accounts, update_mode
        )

    def _sync_to_social_accounts(self, update_mode: int):
        def params_for_platform(params, platform):
            p = params.copy()
            for k in ["update_id", "reply_to_id"]:
                ks = k + "s"
                if ks in p:
                    d = p.pop(ks)
                    v = d.get(platform + "_id")
                    if v:
                        p[k] = v
            return p

        activate_language_for_user(self.owner.user)
        metadata = self.metadata.copy()

        # backward compatible with previous way of storing mastodon id
        legacy_mastodon_url = self.metadata.pop("shared_link", None)
        if legacy_mastodon_url and not self.metadata.get("mastodon_id"):
            self.metadata["mastodon_id"] = legacy_mastodon_url.split("/")[-1]
            self.metadata["mastodon_url"] = legacy_mastodon_url

        params = self.get_crosspost_params()
        self.sync_to_mastodon(params_for_platform(params, "mastodon"), update_mode)
        self.sync_to_threads(params_for_platform(params, "threads"), update_mode)
        self.sync_to_bluesky(params_for_platform(params, "bluesky"), update_mode)
        if self.metadata != metadata:
            self.save(update_fields=["metadata"])

    def sync_to_bluesky(self, params, update_mode):
        # skip non-public post as Bluesky does not support it
        # update_mode 0 will act like 1 as bsky.app does not support edit
        bluesky = self.owner.user.bluesky
        if params["visibility"] != 0 or not bluesky:
            return False
        if update_mode in [0, 1]:
            post_id = self.metadata.get("bluesky_id")
            if post_id:
                try:
                    bluesky.delete_post(post_id)
                except Exception as e:
                    logger.warning(f"Delete {bluesky} post {post_id} error {e}")
        r = bluesky.post(**params)
        self.metadata.update({"bluesky_" + k: v for k, v in r.items()})
        return True

    def sync_to_threads(self, params, update_mode):
        # skip non-public post as Threads does not support it
        # update_mode will be ignored as update/delete are not supported either
        threads = self.owner.user.threads
        # return
        if params["visibility"] != 0 or not threads:
            return False
        try:
            r = threads.post(**params)
        except RequestAborted:
            logger.warning(f"{self} post to {threads} failed")
            messages.error(threads.user, _("A recent post was not posted to Threads."))
            return False
        self.metadata.update({"threads_" + k: v for k, v in r.items()})
        return True

    def sync_to_mastodon(self, params, update_mode):
        mastodon = self.owner.user.mastodon
        if not mastodon:
            return False
        if self.owner.user.preference.mastodon_repost_mode == 1:
            if update_mode == 1:
                toot_id = self.metadata.pop("mastodon_id", None)
                if toot_id:
                    self.metadata.pop("mastodon_url", None)
                    mastodon.delete_post(toot_id)
            elif update_mode == 1:
                params.pop("update_id", None)
            return self.crosspost_to_mastodon(params)
        elif self.latest_post:
            mastodon.boost(self.latest_post.url)
        else:
            logger.warning("No post found for piece")
        return True

    def crosspost_to_mastodon(self, params):
        mastodon = self.owner.user.mastodon
        if not mastodon:
            return False
        try:
            r = mastodon.post(**params)
        except PermissionDenied:
            messages.error(
                mastodon.user,
                _("A recent post was not posted to Mastodon, please re-authorize."),
                meta={"url": mastodon.get_reauthorize_url()},
            )
            return False
        except RequestAborted:
            logger.warning(f"{self} post to {mastodon} failed")
            messages.error(
                mastodon.user, _("A recent post was not posted to Mastodon.")
            )
            return False
        self.metadata.update({"mastodon_" + k: v for k, v in r.items()})
        return True

    def get_ap_data(self):
        return {
            "object": {
                "tag": (
                    [self.item.ap_object_ref]  # type:ignore
                    if hasattr(self, "item")
                    else []
                ),
                "relatedWith": [self.ap_object],
            }
        }

    def sync_to_timeline(self, update_mode: int = 0):
        """update_mode: 0 update if exists otherwise create; 1: delete if exists and create; 2: only create"""
        user = self.owner.user
        v = Takahe.visibility_n2t(self.visibility, user.preference.post_public_mode)
        existing_post = self.latest_post
        if existing_post:
            if (
                existing_post.state in ["deleted", "deleted_fanned_out"]
                or update_mode == 2
            ):
                existing_post = None
            elif update_mode == 1:
                Takahe.delete_posts([existing_post.pk])
                existing_post = None
        params = {
            "author_pk": self.owner.pk,
            "visibility": v,
            "post_pk": existing_post.pk if existing_post else None,
            "post_time": self.created_time,  # type:ignore subclass must have this
            "edit_time": self.edited_time,  # type:ignore subclass must have this
            "data": self.get_ap_data(),
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
