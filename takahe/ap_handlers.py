from datetime import datetime
from time import sleep

from loguru import logger

from catalog.common import *
from journal.models import Comment, Piece, Rating, Review, ShelfMember
from users.models import User as NeoUser

from .models import Follow, Identity, Post
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
}


def _parse_items(objects):
    logger.debug(f"Parsing item links from {objects}")
    if not objects:
        return []
    objs = objects if isinstance(objects, list) else [objects]
    items = [obj for obj in objs if obj["type"] in _supported_ap_catalog_item_types]
    return items


def _parse_piece_objects(objects):
    logger.debug(f"Parsing pieces from {objects}")
    if not objects:
        return []
    objs = objects if isinstance(objects, list) else [objects]
    pieces = []
    for obj in objs:
        if obj["type"] in _supported_ap_journal_types.keys():
            pieces.append(obj)
        else:
            logger.warning(f'Unknown link type {obj["type"]}')
    return pieces


def _get_or_create_item(item_obj):
    logger.debug(f"Fetching item by ap from {item_obj}")
    typ = item_obj["type"]
    url = item_obj["href"]
    if typ in ["TVEpisode", "PodcastEpisode"]:
        # TODO support episode item
        # match and fetch parent item first
        logger.debug(f"{typ}:{url} not supported yet")
        return None
    site = SiteManager.get_site_by_url(url)
    if not site:
        logger.warning(f"Site not found for {url}")
        return None
    site.get_resource_ready()
    item = site.get_item()
    if not item:
        logger.warning(f"Item not fetched for {url}")
    return item


def _get_visibility(post_visibility):
    match post_visibility:
        case 2:
            return 1
        case 3:
            return 2
        case _:
            return 0


def post_fetched(pk, obj):
    post = Post.objects.get(pk=pk)
    owner = Takahe.get_or_create_remote_apidentity(post.author)
    if not post.type_data:
        logger.warning(f"Post {post} has no type_data")
        return
    ap_object = post.type_data.get("object", {})
    items = _parse_items(ap_object.get("tag"))
    pieces = _parse_piece_objects(ap_object.get("relatedWith"))
    logger.info(f"Post {post} has items {items} and pieces {pieces}")
    if len(items) == 0:
        logger.warning(f"Post {post} has no remote items")
        return
    elif len(items) > 1:
        logger.warning(f"Post {post} has more than one remote item")
        return
    item = _get_or_create_item(items[0])
    if not item:
        logger.warning(f"Post {post} has no local item matched or created")
        return
    for p in pieces:
        cls = _supported_ap_journal_types.get(p["type"])
        if not cls:
            logger.warning(f'Unknown link type {p["type"]}')
            continue
        cls.update_by_ap_object(owner, item, p, pk, _get_visibility(post.visibility))


def post_deleted(pk, obj):
    for piece in Piece.objects.filter(posts__id=pk, local=False):
        # delete piece if the deleted post is the most recent one for the piece
        if piece.latest_post_id == pk:
            logger.debug(f"Deleting remote piece {piece}")
            piece.delete()
        else:
            logger.debug(f"Matched remote piece {piece} has newer posts, not deleting")


def identity_fetched(pk):
    try:
        identity = Identity.objects.get(pk=pk)
    except Identity.DoesNotExist:
        sleep(2)
        try:
            identity = Identity.objects.get(pk=pk)
        except Identity.DoesNotExist:
            logger.warning(f"Fetched identity {pk} not found")
            return
    if identity.username and identity.domain:
        apid = Takahe.get_or_create_remote_apidentity(identity)
        if apid:
            logger.debug(f"Identity {identity} synced")
        else:
            logger.warning(f"Identity {identity} not synced")
    else:
        logger.warning(f"Identity {identity} has no username or domain")
