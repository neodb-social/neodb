import re
import uuid
from abc import abstractmethod
from collections.abc import Sequence
from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING, Any, Self

import django_rq
from atproto_client.request import exceptions

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

from catalog.models import (
    AvailableItemCategory,
    Item,
    ItemCategory,
    item_categories,
    item_content_types,
)
from common.sentry import count as sentry_count
from takahe.utils import Takahe
from users.middlewares import activate_language_for_user
from users.models import APIdentity, User

from ..search import JournalIndex
from .mixins import UserOwnedObjectMixin

if TYPE_CHECKING:
    from takahe.models import Post

    from .atproto import AtprotoRecord
    from .itemlist import ListMember
    from .like import Like


class VisibilityType(models.IntegerChoices):
    Public = 0, _("Public")  # ty: ignore[invalid-assignment]
    Follower_Only = 1, _("Followers Only")  # ty: ignore[invalid-assignment]
    Private = 2, _("Mentioned Only")  # ty: ignore[invalid-assignment]


def q_owned_piece_visible_to_user(
    viewing_user: User | None, owner: APIdentity, check_blocking: bool = False
) -> Q:
    """return a Q object to filter pieces that are visible to the viewing user"""
    if check_blocking and owner.restricted:
        return Q(pk__in=[])
    if not viewing_user or not viewing_user.is_authenticated:
        if owner.anonymous_viewable:
            return Q(owner=owner, visibility=0)
        else:
            return Q(pk__in=[])
    viewer = viewing_user.identity
    if viewer == owner:
        return Q(owner=owner)
    elif check_blocking and viewer.is_blocked_by(owner):
        return Q(pk__in=[])
    elif viewer.is_following(owner):
        return Q(owner=owner, visibility__in=[0, 1])
    else:
        return Q(owner=owner, visibility=0)


def q_owned_parent_piece_visible_to_user(
    viewing_user: User | None, owner: APIdentity, check_blocking: bool = False
) -> Q:
    """return a Q object to filter pieces that are visible to the viewing user"""
    if check_blocking and owner.restricted:
        return Q(pk__in=[])
    if not viewing_user or not viewing_user.is_authenticated:
        if owner.anonymous_viewable:
            return Q(parent__owner=owner, visibility=0)
        else:
            return Q(pk__in=[])
    viewer = viewing_user.identity
    if viewer == owner:
        return Q(parent__owner=owner)
    elif check_blocking and viewer.is_blocked_by(owner):
        return Q(pk__in=[])
    elif viewer.is_following(owner):
        return Q(parent__owner=owner, visibility__in=[0, 1])
    else:
        return Q(parent__owner=owner, visibility=0)


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
    from takahe.models import Identity as TakaheIdentity

    restricted_ids = list(
        TakaheIdentity.objects.filter(restriction__gt=0).values_list("pk", flat=True)
    )
    if not viewing_user or not viewing_user.is_authenticated:
        if restricted_ids:
            return Q(visibility=0, owner__anonymous_viewable=True) & ~Q(
                owner_id__in=restricted_ids
            )
        return Q(visibility=0, owner__anonymous_viewable=True)
    viewer = viewing_user.identity
    following = viewer.following
    base_q = (
        Q(visibility=0)
        | Q(owner_id__in=following, visibility=1)
        | Q(owner_id=viewer.pk)
    ) & ~Q(owner_id__in=viewer.ignoring)
    if not restricted_ids:
        return base_q
    non_followed_restricted = list(set(restricted_ids) - set(following) - {viewer.pk})
    if not non_followed_restricted:
        return base_q
    return base_q & ~Q(owner_id__in=non_followed_restricted)


def q_piece_in_home_feed_of_user(viewing_user: User):
    viewer = viewing_user.identity
    return Q(owner_id__in=viewer.following, visibility__lt=2) | Q(owner_id=viewer.pk)


def q_item_in_category(item_category: ItemCategory | AvailableItemCategory):
    classes = item_categories()[ItemCategory(item_category)]
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
        post_relations: models.QuerySet["PiecePost"]
    url_path = "p"  # subclass must specify this
    uid = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    local = models.BooleanField(default=True)
    posts = models.ManyToManyField(
        "takahe.Post", related_name="pieces", through="PiecePost"
    )
    previous_visibility: int | None = None
    post_when_save: bool = False
    crosspost_when_save: bool = False
    index_when_save: bool = False
    application_id_when_save: int | None = None

    @property
    def classname(self) -> str:
        return self.__class__.__name__.lower()

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        if "visibility" in field_names:
            # avoid hasattr(instance, "visibility") which may cause RecursionError
            instance.previous_visibility = instance.visibility
        return instance

    def save(self, *args, **kwargs):
        link_post_id = kwargs.pop("link_post_id", -1)
        post_when_save = kwargs.pop(
            "post_when_save", self.local and self.post_when_save
        )
        index_when_save = kwargs.pop("index_when_save", self.index_when_save)
        super().save(*args, **kwargs)
        if link_post_id is None:
            self.clear_post_ids()
        elif link_post_id != -1:
            self.link_post_id(link_post_id)
        if post_when_save:
            visibility_changed = self.previous_visibility != self.visibility
            self.previous_visibility = self.visibility
            update_mode = 1 if visibility_changed else 0
            self.sync_to_timeline(update_mode)
            if self.crosspost_when_save:
                self.sync_to_social_accounts(update_mode)
        if index_when_save:
            self.update_index()

    def delete(self, *args, **kwargs):
        if self.local:
            self.delete_from_timeline()
            self.delete_crossposts()
        if self.local or self.index_when_save:
            self.delete_index()
        return super().delete(*args, **kwargs)

    @property
    def uuid(self):
        return b62_encode(self.uid.int).zfill(22)

    @property
    def url(self):
        return f"/{self.url_path}/{self.uuid}"

    @property
    def absolute_url(self):
        return settings.SITE_INFO["site_url"] + self.url

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
    def get_by_url_and_owner(cls, url_or_b62, owner_id):
        b62 = url_or_b62.strip().split("/")[-1]
        if len(b62) not in [21, 22]:
            r = re.search(r"[A-Za-z0-9]{21,22}", url_or_b62)
            if r:
                b62 = r[0]
        try:
            obj = cls.objects.get(uid=uuid.UUID(int=b62_decode(b62)), owner_id=owner_id)
        except Exception:
            obj = None
        return obj

    @classmethod
    def get_by_post_id(cls, post_id: int):
        pp = PiecePost.objects.filter(post_id=post_id).first()
        return pp.piece if pp else None

    def _invalidate_post_caches(self) -> None:
        # ``latest_post_id`` / ``latest_post`` / ``all_post_ids`` are
        # ``cached_property``s over ``post_relations``; call this after
        # mutating PiecePost rows so the next access re-queries.
        for attr in ("latest_post_id", "latest_post", "all_post_ids"):
            self.__dict__.pop(attr, None)

    def link_post_id(self, post_id: int):
        PiecePost.objects.get_or_create(piece=self, post_id=post_id)
        self._invalidate_post_caches()

    def clear_post_ids(self):
        PiecePost.objects.filter(piece=self).delete()
        self._invalidate_post_caches()

    @cached_property
    def latest_post_id(self):
        return (
            self.post_relations.order_by("-pk")
            .values_list("post_id", flat=True)
            .first()
        )

    @cached_property
    def latest_post(self) -> "Post | None":
        pk = self.latest_post_id
        return Takahe.get_posts([pk]).first() if pk else None

    @cached_property
    def all_post_ids(self):
        post_ids = list(self.post_relations.values_list("post_id", flat=True))
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
        """
        returns a dict of parameter to create a post
        """
        return {}

    @abstractmethod
    def to_crosspost_params(self) -> dict[str, Any]:
        """
        returns a dict of parameter to create a post for each platform
        "content" - required, may contain ##obj## / ##obj_link_if_plain## / ##rating##
        ...
        """
        return {}

    def atproto_collections(self) -> set[str]:
        """ATProto collections (NSIDs) this piece manages on the owner's PDS.

        Every collection listed here is reconciled on sync: its record is
        written when present in :meth:`to_atproto_records`, or deleted
        otherwise. The default manages nothing.
        """
        return set()

    def atproto_rkey(self) -> str:
        """Record key for this piece's PDS records: the piece's own uuid.

        Derivable from the piece itself (no stored state), stable across
        edits and across item merges (unlike a subject-derived key), and
        distinct pieces -- e.g. future multiple reviews of one work -- map
        to distinct records.
        """
        return self.uuid

    def to_atproto_records(self) -> "list[AtprotoRecord]":
        """Structured ``net.neodb.*`` records that should currently exist on
        the owner's PDS, as ``(collection, record)`` pairs.

        The record key comes from :meth:`atproto_rkey`, so records are
        reconstructable and never need to be tracked in the database; updates
        overwrite in place. Subclasses with a portable representation (review,
        mark) override this; the default publishes nothing.
        """
        return []

    @classmethod
    def update_by_ap_object(
        cls,
        owner: APIdentity,
        item: Item,
        obj,
        post: "Post",
        crosspost: bool | None = False,
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
            edited = (
                post.edited
                if local
                else datetime.fromisoformat(obj.get("updated") or obj["published"])
            )
            if p.edited_time >= edited:
                # incoming ap object is older than what we have, no update needed
                return p
            d["edited_time"] = edited
            for k, v in d.items():
                setattr(p, k, v)
            if crosspost is not None:
                p.crosspost_when_save = crosspost
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
                d["edited_time"] = datetime.fromisoformat(
                    obj.get("updated") or obj["published"]
                )
            p = cls(**d)
            if crosspost is not None:
                p.crosspost_when_save = crosspost
            p.previous_visibility = visibility
            p.save(link_post_id=post.id)
        # subclass may have to add additional code to update type_data in local post
        return p

    @classmethod
    def _delete_crossposts(cls, user_pk, metadata: dict, record_refs=None):
        user = User.objects.get(pk=user_pk)
        toot_id = metadata.get("mastodon_id")
        if toot_id and user.mastodon:
            user.mastodon.delete_post(toot_id)
        post_id = metadata.get("bluesky_id")
        if post_id and user.bluesky:
            try:
                user.bluesky.delete_post(post_id)
            except Exception as e:
                logger.warning(f"Delete {user.bluesky} post {post_id} error {e}")
        if record_refs and user.bluesky:
            for collection, rkey in record_refs:
                try:
                    user.bluesky.delete_record(collection, rkey)
                except Exception as e:
                    logger.warning(
                        f"Delete {user.bluesky} record {collection}/{rkey} error {e}"
                    )

    def delete_crossposts(self):
        metadata = (
            self.metadata.copy() if hasattr(self, "metadata") and self.metadata else {}
        )
        # record keys are derived here while the piece still exists so the
        # async job can clean up the PDS without stored state.
        # not gated on metadata: records may exist even when no skeet was
        # ever posted (e.g. the feed post failed but put_record succeeded)
        record_refs = (
            [
                [collection, self.atproto_rkey()]
                for collection in self.atproto_collections()
            ]
            if self.owner.user_id and self.owner.user.bluesky
            else []
        )
        if metadata or record_refs:
            django_rq.get_queue("mastodon").enqueue(
                self._delete_crossposts,
                self.owner.user_id,
                metadata,
                record_refs,
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
            # do not trigger sync or index again
            self.save(
                update_fields=["metadata"], post_when_save=False, index_when_save=False
            )

    def sync_to_bluesky(self, params, update_mode):
        # skip non-public post as Bluesky does not support it
        # update_mode 0 will act like 1 as bsky.app does not support edit
        bluesky = self.owner.user.bluesky
        if not bluesky:
            return False
        if params["visibility"] != 0:
            # piece is no longer public: drop any records previously written
            # to the PDS, since PDS records are world-readable
            self._sync_records_to_bluesky(bluesky, drop=True)
            return False
        if update_mode in [0, 1]:
            post_id = self.metadata.get("bluesky_id")
            if post_id:
                try:
                    bluesky.delete_post(post_id)
                except Exception as e:
                    logger.warning(f"Delete {bluesky} post {post_id} error {e}")
        r = None
        attrs = {"platform": "bluesky", "mode": "post"}
        try:
            r = bluesky.post(**params)
        except (exceptions.UnauthorizedError, exceptions.BadRequestError) as e:
            if isinstance(e, exceptions.UnauthorizedError) or "ExpiredToken" in str(e):
                # re-authorize if ATProto token is expired
                messages.error(
                    bluesky.user,
                    _(
                        "A recent post was not posted to Bluesky, please login NeoDB using ATProto again to re-authorize."
                    ),
                    meta={
                        "url": settings.SITE_INFO["site_url"]
                        + "/account/login?method=atproto"
                    },
                )
                logger.warning(f"{self} post to {bluesky} failed with auth issue: {e}")
            else:
                logger.warning(f"{self} post to {bluesky} failed: {e}")
        except Exception as e:
            logger.warning(f"Post to {bluesky} error {e}")
        if r:
            self.metadata.update({"bluesky_" + k: v for k, v in r.items()})
            sentry_count("crosspost.success", attributes=attrs)
        else:
            sentry_count("crosspost.failure", attributes=attrs)
        self._sync_records_to_bluesky(bluesky)
        return True

    def _sync_records_to_bluesky(self, bluesky, drop: bool = False):
        """Reconcile this piece's net.neodb.* records on the owner's PDS.

        For every collection the piece manages, the record is written (keyed
        by :meth:`atproto_rkey`, so put_record overwrites in place on edit)
        when present, or deleted otherwise -- including the case where the
        piece is no longer public (``drop``). No state is stored: records are
        reconstructable from the piece, and put/delete are both idempotent.
        """
        collections = self.atproto_collections()
        if not collections:
            return
        rkey = self.atproto_rkey()
        try:
            present = (
                {} if drop else {c: record for c, record in self.to_atproto_records()}
            )
        except Exception as e:
            logger.warning(f"{self} build atproto records error {e}")
            return
        for collection in collections:
            try:
                if collection in present:
                    bluesky.put_record(collection, rkey, present[collection])
                else:
                    bluesky.delete_record(collection, rkey)
            except Exception as e:
                logger.warning(
                    f"{self} sync record {collection} to {bluesky} error {e}"
                )

    def sync_to_threads(self, params, update_mode):
        # skip non-public post as Threads does not support it
        # update_mode will be ignored as update/delete are not supported either
        threads = self.owner.user.threads
        # return
        if params["visibility"] != 0 or not threads:
            return False
        attrs = {"platform": "threads", "mode": "post"}
        r = None
        try:
            r = threads.post(**params)
        except RequestAborted:
            logger.warning(f"{self} post to {threads} failed")
            messages.error(threads.user, _("A recent post was not posted to Threads."))
        except Exception as e:
            logger.warning(f"Post to {threads} error {e}")
        if r:
            self.metadata.update({"threads_" + k: v for k, v in r.items()})
            sentry_count("crosspost.success", attributes=attrs)
        else:
            sentry_count("crosspost.failure", attributes=attrs)
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
            elif update_mode == 2:
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
        attrs = {"platform": "mastodon", "mode": "post"}
        try:
            r = mastodon.post(**params)
        except PermissionDenied:
            messages.error(
                mastodon.user,
                _("A recent post was not posted to Mastodon, please re-authorize."),
                meta={"url": mastodon.get_reauthorize_url()},
            )
            sentry_count("crosspost.failure", attributes=attrs)
            return False
        except RequestAborted:
            logger.warning(f"{self} post to {mastodon} failed")
            messages.error(
                mastodon.user, _("A recent post was not posted to Mastodon.")
            )
            sentry_count("crosspost.failure", attributes=attrs)
            return False
        self.metadata.update({"mastodon_" + k: v for k, v in r.items()})
        sentry_count("crosspost.success", attributes=attrs)
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

    def delete_from_timeline(self):
        Takahe.delete_posts(self.all_post_ids)

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
            "language": user.macrolanguage,
            "application_id": self.application_id_when_save,
        }
        params.update(self.to_post_params())
        post = Takahe.post(**params)  # ty: ignore[invalid-argument-type]
        if post and post != existing_post:
            self.link_post_id(post.pk)
        return post

    def update_index(self):
        index = JournalIndex.instance()
        doc = index.piece_to_doc(self)
        if doc:
            try:
                index.delete_by_piece([self.pk])
                index.replace_docs([doc])
            except Exception as e:
                logger.error(f"Indexing {self} error {e}")

    def delete_index(self):
        index = JournalIndex.instance()
        index.delete_by_piece([self.pk])

    def to_indexable_doc(self) -> dict[str, Any]:
        raise NotImplementedError(
            f"{self.__class__} should override this to make itself searchable"
        )


class PiecePost(models.Model):
    post_id: int
    piece = models.ForeignKey(
        Piece, on_delete=models.CASCADE, related_name="post_relations"
    )
    post = models.ForeignKey(
        "takahe.Post", db_constraint=False, db_index=True, on_delete=models.DO_NOTHING
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["piece", "post"], name="unique_piece_post"),
        ]


def prefetch_latest_posts(pieces: Sequence["Piece"]) -> None:
    """Batch-prefetch latest_post_id and latest_post for a list of Piece objects.

    Avoids N+1 queries when templates access piece.latest_post, which would
    otherwise trigger one PiecePost query and one Post query per piece.

    Latest is picked by ``PiecePost.pk`` (monotonic per insertion),
    matching ``Piece.latest_post_id``'s logic.
    """
    if not pieces:
        return
    piece_ids = [p.pk for p in pieces]
    # For each piece pick the PiecePost row with the largest pk
    # (=most-recently inserted link), then grab its post_id.
    piece_to_latest: dict[int, int] = dict(
        PiecePost.objects.filter(piece_id__in=piece_ids)
        .order_by("piece_id", "-pk")
        .distinct("piece_id")
        .values_list("piece_id", "post_id")
    )
    all_post_ids = list(piece_to_latest.values())
    posts_by_id = (
        {p.pk: p for p in Takahe.get_posts(all_post_ids)} if all_post_ids else {}
    )
    for piece in pieces:
        post_id = piece_to_latest.get(piece.pk)
        piece.__dict__["latest_post_id"] = post_id
        piece.__dict__["latest_post"] = posts_by_id.get(post_id) if post_id else None


def prefetch_pieces_for_posts(posts: list["Post"]) -> None:
    """Batch-prefetch piece and item for a list of Post objects to avoid N+1 queries."""
    from catalog.models import Item
    from journal.models import ShelfMember

    if not posts:
        return
    post_ids = [p.pk for p in posts]
    # Collect piece IDs grouped by post from the through table
    piece_ids_by_post: dict[int, list[int]] = {}
    all_piece_ids: set[int] = set()
    for rel in PiecePost.objects.filter(post_id__in=post_ids).values(
        "post_id", "piece_id"
    ):
        piece_ids_by_post.setdefault(rel["post_id"], []).append(rel["piece_id"])
        all_piece_ids.add(rel["piece_id"])
    # Fetch all pieces via polymorphic manager to get concrete types
    pieces_by_id: dict[int, Piece] = {
        p.pk: p for p in Piece.objects.filter(pk__in=all_piece_ids)
    }
    # Resolve piece per post (prefer ShelfMember when multiple)
    all_pieces: list[Piece | None] = []
    item_ids: set[int] = set()
    for post in posts:
        pids = piece_ids_by_post.get(post.pk, [])
        pcs = [pieces_by_id[pid] for pid in pids if pid in pieces_by_id]
        if len(pcs) == 1:
            piece = pcs[0]
        else:
            piece = next((p for p in pcs if p.__class__ == ShelfMember), None)
        post.__dict__["piece"] = piece
        all_pieces.append(piece)
        item_id = getattr(piece, "item_id", None) if piece else None
        if item_id:
            item_ids.add(item_id)
    # Batch-fetch items
    items_by_id = {i.pk: i for i in Item.objects.filter(pk__in=item_ids)}
    for post, piece in zip(posts, all_pieces):
        item_id = getattr(piece, "item_id", None) if piece else None
        if item_id:
            item = items_by_id.get(item_id)
            if item:
                setattr(piece, "item", item)
            post.__dict__["item"] = item
        else:
            post.__dict__["item"] = None


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
    if TYPE_CHECKING:
        owner_id: int
        item_id: int

    owner = models.ForeignKey(APIdentity, on_delete=models.PROTECT)
    visibility = models.PositiveSmallIntegerField(
        choices=VisibilityType.choices, default=0, null=False
    )
    created_time = models.DateTimeField(default=timezone.now)
    edited_time = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    remote_id = models.CharField(max_length=200, null=True, default=None)

    def __str__(self):
        return f"{self.__class__.__name__}:{self.uuid}@{self.item}"

    @property
    def display_title(self) -> str:
        raise NotImplementedError("subclass should override this")

    @property
    def brief_description(self) -> str:
        raise NotImplementedError("subclass should override this")

    class Meta:
        abstract = True


class Debris(Content):
    class_name = CharField(max_length=50)

    class Meta:
        indexes = [
            models.Index(fields=["remote_id"], name="debris_remote_id_idx"),
        ]

    @classmethod
    def create_from_piece(cls, c: "Content | ListMember"):
        return cls.objects.create(
            class_name=c.__class__.__name__,
            owner=c.owner,
            visibility=c.visibility,
            created_time=c.created_time,
            metadata=c.ap_object,
            item=c.item,
            remote_id=c.remote_id if hasattr(c, "remote_id") else None,
        )

    def to_indexable_doc(self) -> dict[str, Any]:
        return {}
