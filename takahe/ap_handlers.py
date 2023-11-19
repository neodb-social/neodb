from datetime import datetime

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
    "Performance",
    "PerformanceProduction",
]

_supported_ap_journal_types = {
    "Status": ShelfMember,
    "Rating": Rating,
    "Comment": Comment,
    "Review": Review,
}


def _parse_item_links(objects):
    logger.debug(f"Parsing item links from {objects}")
    if not objects:
        return []
    objs = objects if isinstance(objects, list) else [objects]
    items = [
        obj["href"] for obj in objs if obj["type"] in _supported_ap_catalog_item_types
    ]
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


def _get_or_create_item_by_ap_url(url):
    logger.debug(f"Fetching item by ap from {url}")
    site = SiteManager.get_site_by_url(url)
    if not site:
        return None
    site.get_resource_ready()
    item = site.get_item()
    return item


def _get_visibility(post_visibility):
    match post_visibility:
        case 2:
            return 1
        case 3:
            return 2
        case _:
            return 0


def _update_or_create_post(pk, obj):
    post = Post.objects.get(pk=pk)
    owner = Takahe.get_or_create_remote_apidentity(post.author)
    if not post.type_data:
        logger.warning(f"Post {post} has no type_data")
        return
    items = _parse_item_links(post.type_data["object"]["tag"])
    pieces = _parse_piece_objects(post.type_data["object"]["relatedWith"])
    logger.info(f"Post {post} has items {items} and pieces {pieces}")
    if len(items) == 0:
        logger.warning(f"Post {post} has no remote items")
        return
    elif len(items) > 1:
        logger.warning(f"Post {post} has more than one remote item")
        return
    remote_url = items[0]
    item = _get_or_create_item_by_ap_url(remote_url)
    if not item:
        logger.warning(f"Post {post} has no local item")
        return
    for p in pieces:
        cls = _supported_ap_journal_types[p["type"]]
        cls.update_by_ap_object(owner, item, p, pk, _get_visibility(post.visibility))


def post_fetched(pk, obj):
    _update_or_create_post(pk, obj)


def post_deleted(pk, obj):
    Piece.objects.filter(posts__id=pk, local=False).delete()


def identity_fetched(pk):
    identity = Takahe.get_identity(pk)
    if identity.username and identity.domain:
        apid = Takahe.get_or_create_remote_apidentity(identity)
        if apid:
            logger.debug(f"Identity {identity} synced")
        else:
            logger.warning(f"Identity {identity} not synced")
    else:
        logger.warning(f"Identity {identity} has no username or domain")
