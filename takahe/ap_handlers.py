from time import sleep
from typing import Any

from django.conf import settings
from loguru import logger

from catalog.common import *
from common.utils import discord_send
from journal.models import (
    Comment,
    Note,
    Piece,
    PieceInteraction,
    Rating,
    Review,
    ShelfMember,
)
from journal.models.index import JournalIndex
from users.middlewares import activate_language_for_user
from users.models.apidentity import APIdentity

from .models import Identity, Post, Report, TimelineEvent
from .utils import Takahe

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

_supported_ap_journal_types = {
    "Status": ShelfMember,
    "Rating": Rating,
    "Comment": Comment,
    "Review": Review,
    "Note": Note,
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


def _get_or_create_item(item_obj) -> Item | None:
    typ = item_obj["type"]
    url = item_obj["href"]
    if url.startswith(settings.SITE_INFO["site_url"]):
        logger.debug(f"Matching local item from {item_obj}")
        item = Item.get_by_url(url, True)
        if not item:
            logger.warning(f"Item not found for {url}")
        return item
    logger.debug(f"Fetching item by ap from {item_obj}")
    if typ in ["TVEpisode", "PodcastEpisode"]:
        # TODO support episode item
        # match and fetch parent item first
        logger.debug(f"{typ}:{url} not supported yet")
        return None
    site = SiteManager.get_site_by_url(url)
    if not site:
        logger.warning(f"Site not found for {url}")
        return None
    try:
        site.get_resource_ready()
    except Exception:
        # occationally race condition happens and resource is fetched by another process,
        # so we clear cache to retry matching the resource
        site.clear_cache()
    item = site.get_item()
    if not item:
        logger.error(f"Item not fetched for {url}")
    return item


def post_created(pk, post_data):
    return _post_fetched(pk, True, post_data)


def post_edited(pk, post_data):
    return _post_fetched(pk, True, post_data, False)


def post_fetched(pk, post_data):
    return _post_fetched(pk, False, post_data, True)


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
    owner = Takahe.get_or_create_remote_apidentity(post.author)
    if local:
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
        if not post.type_data and not post_data:
            logger.warning(f"Remote post {post} has no type_data")
            return
        ap_objects = post_data or post.type_data.get("object", {})
        items = _parse_items(ap_objects.get("tag"))
        pieces = _parse_piece_objects(ap_objects.get("relatedWith"))
    if len(items) == 0:
        logger.warning(f"Post {post} has no items")
        return
    elif len(items) > 1:
        logger.warning(f"Post {post} has more than one item")
        return
    logger.info(f"Post {post} has items {items} and pieces {pieces}")
    item = _get_or_create_item(items[0])
    if not item:
        logger.warning(f"Post {post} has no local item matched or created")
        return
    for p in pieces:
        cls = _supported_ap_journal_types.get(p["type"])
        if not cls:
            logger.warning(f"Unknown link type {p['type']}")
            continue
        cls.update_by_ap_object(owner, item, p, post)


def post_deleted(pk, local, post_data):
    for piece in Piece.objects.filter(posts__id=pk):
        if piece.local and piece.__class__ != Note:
            # no delete other than Note, for backward compatibility, should reconsider later
            return
        # delete piece if the deleted post is the most recent one for the piece
        if piece.latest_post_id == pk:
            logger.debug(f"Deleting piece {piece}")
            piece.delete()
        else:
            logger.debug(f"Matched piece {piece} has newer posts, not deleting")


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
    if apid.user and apid.user.is_active:
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
    discord_send(
        "report",
        f"{report.complaint}\n\nabout post:{report.subject_post.absolute_object_uri()}\n{report.subject_post.content}",
        thread_name=f"[{report.type}] Report received about {report.subject_identity.username}",
        username=f"@{report.source_identity.handle}",
    )
