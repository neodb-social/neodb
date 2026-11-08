import logging
import re
from datetime import UTC, datetime
from time import sleep
from typing import Any
from urllib.parse import urlparse


from catalog.bookwyrm import resolve_book
from catalog.models import Item
from common.sentry import count as sentry_count
from common.sentry import url_domain
from common.utils import discord_send
from journal.models import (
    Article,
    Collection,
    Comment,
    Note,
    Piece,
    PieceInteraction,
    Rating,
    Review,
    Shelf,
    ShelfMember,
    cleanup_deleted_post,
)
from journal.search import JournalIndex
from users.middlewares import activate_language_for_user
from users.models.apidentity import APIdentity

from .models import Identity, Post, Report, TimelineEvent
from .utils import Takahe

logger = logging.getLogger(__name__)

_supported_ap_catalog_item_types = [
    "Edition",
    "Movie",
    "TVShow",
    "TVSeason",
    "TVEpisode",
    "Album",
    "Game",
    "Podcast",
    "PodcastEpisode",
    "Performance",
    "PerformanceProduction",
]


class _ShelfDispatcher:
    """Inbound dispatcher for the shared ``"Shelf"`` AP wire type.

    Both NeoDB Collection and NeoDB Shelf serialize as ``type: "Shelf"``.
    Receiver routes by envelope shape: ``shelfType`` present → NeoDB
    Shelf; absent → NeoDB Collection.
    """

    @classmethod
    def update_by_ap_object(cls, owner, item, obj, post, crosspost=None):
        if obj.get("shelfType"):
            return Shelf.update_by_ap_object(owner, item, obj, post, crosspost)
        return Collection.update_by_ap_object(owner, item, obj, post, crosspost)


_supported_ap_journal_types = {
    "Status": ShelfMember,
    "Rating": Rating,
    "Comment": Comment,
    "Review": Review,
    "Note": Note,
    "Shelf": _ShelfDispatcher,
}


def _parse_items(objects) -> list[dict[str, Any]]:
    logger.debug(f"Parsing item links from {objects}")
    if not objects:
        return []
    objs = objects if isinstance(objects, list) else [objects]
    items = [obj for obj in objs if obj["type"] in _supported_ap_catalog_item_types]
    return items


def _parse_piece_objects(objects) -> list[dict[str, Any]]:
    logger.debug(f"Parsing pieces from {objects}")
    if not objects:
        return []
    objs = objects if isinstance(objects, list) else [objects]
    pieces = []
    for obj in objs:
        if obj["type"] in _supported_ap_journal_types.keys():
            pieces.append(obj)
        else:
            logger.warning(f"Unknown link type {obj['type']}")
    return pieces


# BookWyrm sends reviews to other software as an Article named, in the
# author's language, 'Review of "<book>" (<n> stars): <headline>', or without
# the rating part when unrated; a missing headline renders as "None".
_BOOKWYRM_RATED_NAME = re.compile(r"^.*?\(\s*\d+(?:[.,]\d+)?[^()]*\)\s*:\s*(.*)$", re.S)
_BOOKWYRM_UNRATED_NAME = re.compile(r"^.*?[\"\u201d\u00bb]\s*:\s*(.*)$", re.S)


def bookwyrm_review_title(name: str, rated: bool) -> str:
    name = name.strip()
    m = (_BOOKWYRM_RATED_NAME if rated else _BOOKWYRM_UNRATED_NAME).match(name)
    headline = m[1].strip() if m else ""
    if headline and headline != "None":
        return headline[:500]
    return re.sub(r"\s*:\s*None$", "", name)[:500]


def is_bookwyrm_review(post: Any, obj: dict[str, Any]) -> bool:
    return post.type == "Article" and isinstance(obj.get("inReplyToBook"), str)


# Rating and Review save edited_time as the local save time, so the remote
# version a piece holds is kept in its metadata to order later versions.
_REMOTE_UPDATED = "remote_updated"


def _remote_updated(p: Rating | Review) -> datetime | None:
    v = (p.metadata or {}).get(_REMOTE_UPDATED)
    try:
        return datetime.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def _note_remote_updated(
    p: Rating | Review | None, remote_id: str, updated: datetime
) -> None:
    if p is None or p.remote_id != remote_id:
        return
    known = _remote_updated(p)
    if known is None or updated > known:
        p.metadata = {**(p.metadata or {}), _REMOTE_UPDATED: updated.isoformat()}
        type(p).objects.filter(pk=p.pk).update(metadata=p.metadata)


def _delete_older(
    cls: type[Rating] | type[Review],
    owner: APIdentity,
    item: Item,
    remote_id: str,
    updated: datetime,
) -> None:
    for p in cls.objects.filter(owner=owner, item=item, remote_id=remote_id):
        known = _remote_updated(p)
        if known is None or updated > known:
            p.delete()


def link_bookwyrm_review(owner: APIdentity, post: Any, obj: dict[str, Any]) -> str:
    """Map a remote BookWyrm review onto a Review and Rating of its book.

    Returns "linked", or why nothing was linked: "no_book" when the book
    cannot be fetched or matched, "spoiler" for an unrated review with a
    content warning, "invalid" for a malformed review.
    """
    review_id = obj.get("id")
    published = obj.get("published")
    updated = obj.get("updated") or published
    if (
        not isinstance(review_id, str)
        or not isinstance(published, str)
        or not isinstance(updated, str)
    ):
        return "invalid"
    try:
        datetime.fromisoformat(published)
        updated_time = datetime.fromisoformat(updated)
        if updated_time.tzinfo is None:
            updated_time = updated_time.replace(tzinfo=UTC)
    except ValueError:
        return "invalid"
    item = resolve_book(obj["inReplyToBook"], urlparse(post.object_uri).hostname)
    if not item:
        logger.info(f"BookWyrm review {review_id}: book not resolved")
        return "no_book"
    rating = obj.get("rating")
    rated = isinstance(rating, (int, float)) and 0 < rating <= 5
    rating_id = review_id + "#rating"
    if rated:
        rating_piece = Rating.update_by_ap_object(
            owner,
            item,
            {
                "id": rating_id,
                "value": rating,
                "best": 5,
                "worst": 0.5,
                "published": published,
                "updated": updated,
            },
            post,
        )
        _note_remote_updated(rating_piece, rating_id, updated_time)
    else:
        # an edit removed the rating; another review's rating stays, and so
        # does one newer than this version, which arrived out of order
        _delete_older(Rating, owner, item, rating_id, updated_time)
    if obj.get("sensitive"):
        # Review has no content warning, so a spoiler stays on the post, and
        # an edit that added the warning drops the copy made before it
        _delete_older(Review, owner, item, review_id, updated_time)
        return "linked" if rated else "spoiler"
    name = obj.get("name")
    content = obj.get("content")
    review = Review.update_by_ap_object(
        owner,
        item,
        {
            "id": review_id,
            "name": bookwyrm_review_title(name if isinstance(name, str) else "", rated)
            or item.display_title,
            "content": content if isinstance(content, str) else "",
            "published": published,
            "updated": updated,
        },
        post,
    )
    _note_remote_updated(review, review_id, updated_time)
    return "linked"


def post_created(pk, post_data):
    return _post_fetched(pk, True, post_data)


def post_edited(pk, post_data):
    return _post_fetched(pk, True, post_data, False)


def post_fetched(pk, post_data):
    return _post_fetched(pk, False, post_data, True)


def _remote_post_domain(post: Any) -> str:
    author = getattr(post, "author", None)
    if not author:
        return "unknown"
    try:
        domain = getattr(author, "uri_domain", None)
    except Exception:
        domain = None
    return domain or url_domain(getattr(author, "actor_uri", None))


def _record_remote_post_fetched(post: Any) -> None:
    sentry_count(
        "post.fetched",
        attributes={"domain": _remote_post_domain(post)},
    )


def _post_fetched(pk, local, post_data, create: bool | None = None):
    retry = 1
    while True:
        try:
            post: Post = Post.objects.get(pk=pk)
            break
        except Post.DoesNotExist:
            if retry > 5:
                logger.error(f"Fetched post {pk} not found")
                return
            sleep(retry)
            retry += 1
    if not local:
        remote_obj = post_data or (post.type_data or {}).get("object") or {}
        if "relatedWith" not in remote_obj and not is_bookwyrm_review(post, remote_obj):
            # takahe hands over every remote Article; leave the others alone
            return
    owner = Takahe.get_or_create_remote_apidentity(post.author)
    if local:
        if post_data and post_data.get("backdated"):
            # An imported post: making a piece from it would save and so
            # republish the post, which is meant to reach no one
            JournalIndex.instance().replace_posts([post])
            return
        activate_language_for_user(owner.user)
        reply_to = post.in_reply_to_post()
        items = []
        pieces = []
        if post_data and "raw_content" in post_data:
            # Local post, extract info for Note if possible
            if (
                reply_to
                and reply_to.author_id == post.author_id
                and reply_to.type_data
                and "object" in reply_to.type_data
                and "relatedWith" in reply_to.type_data["object"]
                # gate on the post author's preference; APIdentity.preference
                # falls back to defaults (enabled) for identities with no
                # local user, e.g. service actors
                and owner.preference.auto_note_on_reply
            ):
                items = _parse_items(reply_to.type_data["object"].get("tag", []))
            elif (
                not create
                and post.type_data
                and "object" in post.type_data
                and "relatedWith" in post.type_data["object"]
            ):
                items = _parse_items(post.type_data["object"].get("tag", []))
            pieces = [{"type": "Note", "content": post_data["raw_content"]}]
        if not items or not pieces:
            # Local post has no related items or usable pieces, update index and move on
            JournalIndex.instance().replace_posts([post])
            return
    else:
        _record_remote_post_fetched(post)
        if not post.type_data and not post_data:
            logger.warning(f"Remote post {post} has no type_data")
            return
        if post_data:
            ap_objects = post_data
        else:
            assert post.type_data is not None
            ap_objects = post.type_data.get("object", {})
        items = _parse_items(ap_objects.get("tag"))
        pieces = _parse_piece_objects(ap_objects.get("relatedWith"))
        # Shelf posts (used for both NeoDB Collection and NeoDB Shelf
        # mirroring) carry only their lightweight envelope here — the
        # ordered member list is fetched separately via the items
        # endpoint. They are mutually exclusive with the single-item
        # pieces (Mark/Review/Note/...) so dispatch them before the
        # single-item enforcement below.
        if is_bookwyrm_review(post, ap_objects) and not pieces:
            link_bookwyrm_review(owner, post, ap_objects)
            return
        shelf_pieces = [p for p in pieces if p["type"] == "Shelf"]
        if shelf_pieces:
            for sp in shelf_pieces:
                _ShelfDispatcher.update_by_ap_object(owner, None, sp, post)
            return
        # Standalone Article: post object itself is the AS Article (no
        # ``relatedWith`` envelope). Reviews-as-Article continue to flow
        # through the items+pieces path below because they always carry
        # ``relatedWith=[Review]``.
        if not pieces and post.type == "Article":
            Article.update_by_ap_object(owner, None, ap_objects, post)
            return
    if len(items) == 0:
        logger.warning(f"Post {post} has no items")
        return
    elif len(items) > 1:
        logger.warning(f"Post {post} has more than one item")
        return
    logger.info(f"Post {post} has items {items} and pieces {pieces}")
    item = Item.get_by_ap_object(items[0])
    if not item:
        logger.warning(f"Post {post} has no local item matched or created")
        return
    remote_marks = []
    for p in pieces:
        cls = _supported_ap_journal_types.get(p["type"])
        if not cls:
            logger.warning(f"Unknown link type {p['type']}")
            continue
        pc = cls.update_by_ap_object(owner, item, p, post)
        if pc and cls in [ShelfMember] and not local:
            remote_marks.append(pc)
    for mark in remote_marks:
        mark.update_index()


def post_deleted(pk, local, post_data):
    cleanup_deleted_post(pk)


def post_interacted(interaction_pk, interaction, post_pk, identity_pk):
    if interaction not in ["like", "boost", "pin"]:
        return
    p = Piece.objects.filter(posts__id=post_pk).first()
    if not p:
        return
    apid = APIdentity.objects.filter(pk=identity_pk).first()
    if not apid:
        logger.warning(f"Identity {identity_pk} not found for interaction")
        return
    if (
        interaction == "boost"
        and p.local
        and p.owner.user.mastodon
        and p.owner.user.mastodon.handle == apid.full_handle
    ):
        # ignore boost by oneself
        TimelineEvent.objects.filter(
            identity_id=p.owner_id,
            type="boosted",
            subject_post_id=post_pk,
            subject_identity_id=identity_pk,
        ).delete()
        return
    PieceInteraction.objects.get_or_create(
        target=p,
        identity_id=identity_pk,
        interaction_type=interaction,
        defaults={"target_type": p.__class__.__name__},
    )


def post_uninteracted(interaction_pk, interaction, post_pk, identity_pk):
    if interaction not in ["like", "boost", "pin"]:
        return
    p = Piece.objects.filter(posts__id=post_pk).first()
    if not p:
        return
    if not APIdentity.objects.filter(pk=identity_pk).exists():
        logger.warning(f"Identity {identity_pk} not found for interaction")
        return
    PieceInteraction.objects.filter(
        target=p,
        identity_id=identity_pk,
        interaction_type=interaction,
    ).delete()


def identity_deleted(pk):
    apid = APIdentity.objects.filter(pk=pk).first()
    if not apid:
        logger.warning(f"APIdentity {apid} not found")
        return

    logger.warning(f"handle deleting identity {apid}")
    if apid.user:
        # Unconditional: the identity is going, so the user must go with it,
        # whatever state the account was in. clear() is safe to repeat, and
        # the old is_active test skipped a suspended account, leaving its
        # tasks, webhooks and social accounts behind.
        apid.user.clear()  # for local identity, clear their user as well
    apid.clear()


def identity_fetched(pk):
    retry = 1
    while True:
        try:
            identity = Identity.objects.get(pk=pk)
            break
        except Identity.DoesNotExist:
            if retry > 5:
                logger.error(f"Fetched identity {pk} not found")
                return
            sleep(retry)
            retry += 1
    if identity.username and identity.domain:
        apid = Takahe.get_or_create_remote_apidentity(identity)
        if apid:
            logger.debug(f"Fetched identity {identity} synced")
        else:
            logger.error(f"Fetched identity {identity} not synced")
    else:
        logger.error(f"Fetched identity {identity} has no username or domain")


def report_received(pk):
    retry = 1
    while True:
        try:
            report = Report.objects.get(pk=pk)
            break
        except Report.DoesNotExist:
            if retry > 5:
                logger.error(f"Report {pk} not found")
                return
            sleep(retry)
            retry += 1
    # an account-only Flag, or a reported post deleted since, has no post
    post = report.subject_post
    about_post = (
        f"\n\nabout post:{post.absolute_object_uri()}\n\n{post.content}" if post else ""
    )
    assert report.subject_identity is not None
    discord_send(
        "report",
        f"{report.complaint}{about_post}",
        thread_name=f"[{report.type}] Report received about {report.subject_identity.username}",
        username=f"@{report.source_identity.handle if report.source_identity else '?@' + str(report.source_domain or '?')}",
    )
