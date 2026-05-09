"""Page-walking inbound sync for federated NeoDB List objects.

Used for both NeoDB ``Collection`` and NeoDB ``Shelf`` mirrors. Given a
local ``List`` mirror with ``remote_id`` set, this job:

1. Issues a signed GET to ``remote_id`` to fetch the Shelf envelope.
2. Walks the ``first``→``next`` chain of ``OrderedCollectionPage`` URLs,
   accumulating ``orderedItems`` entries.
3. Hands the flattened list to ``cls._sync_members_from_ap`` to persist.

Each page-fetch passes through the ``is_valid_url`` SSRF gate; the page
walker bounds total pages to ``MAX_PAGES`` (defense in depth — unbounded
servers could otherwise drive the worker into excessive DB cost).
"""

from __future__ import annotations

import importlib
from datetime import timedelta
from typing import Any

import django_rq
from loguru import logger

from common.validators import is_valid_url
from takahe.auth import sign_get

MAX_FETCH_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=5)
# Soft cap on pages walked per resync. With ``AP_PAGE_SIZE = 100`` this
# bounds a single resync to ~100k items, which is generous; abusive peers
# pushing larger lists are not our problem to absorb in one pass.
MAX_PAGES = 1000


def _resolve_class(class_path: str):
    module_name, _, class_name = class_path.rpartition(".")
    if not module_name:
        raise ValueError(f"Bad class path {class_path!r}")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def _signed_get_json(url: str) -> dict[str, Any] | None:
    """SSRF-gated signed GET that returns the parsed JSON body or None.

    None on any failure (bad URL, transport, non-200, malformed JSON).
    """
    if not is_valid_url(url):
        logger.warning(f"list_sync: refusing unsafe URL {url!r}")
        return None
    try:
        response = sign_get(url)
    except Exception as e:
        logger.warning(f"list_sync: signed GET to {url} failed: {e}")
        return None
    if response.status_code == 404:
        logger.info(f"list_sync: {url} returned 404")
        return None
    if response.status_code != 200:
        logger.warning(f"list_sync: {url} returned {response.status_code}")
        return None
    try:
        return response.json()
    except ValueError as e:
        logger.warning(f"list_sync: bad JSON from {url}: {e}")
        return None


def fetch_remote_list_members(class_path: str, pk: int, attempts: int = 0) -> None:
    """Resync members for one local ``List`` mirror.

    ``class_path`` is the dotted import path of the model class (e.g.
    ``journal.models.collection.Collection`` or
    ``journal.models.shelf.Shelf``) so the same job module serves all
    federated list types.
    """
    cls = _resolve_class(class_path)
    inst = cls.objects.filter(pk=pk, local=False).first()
    if not inst or not inst.remote_id:
        logger.debug(f"list_sync: {class_path}#{pk} gone or no remote_id")
        return
    envelope = _signed_get_json(inst.remote_id)
    if envelope is None:
        _maybe_retry(class_path, pk, attempts)
        return
    items_url = envelope.get("first") or envelope.get("items")
    # Some servers may inline the items list under `orderedItems` even
    # when paginated; prefer it as a fast-path.
    pages_to_walk: list[str] = []
    flat_items: list[dict[str, Any]] = []
    inline = envelope.get("orderedItems")
    if isinstance(inline, list):
        flat_items.extend(e for e in inline if isinstance(e, dict))
    if items_url:
        pages_to_walk.append(items_url)
    visited: set[str] = set()
    pages_walked = 0
    failed_page = False
    while pages_to_walk and pages_walked < MAX_PAGES:
        next_url = pages_to_walk.pop(0)
        if next_url in visited:
            break
        visited.add(next_url)
        page = _signed_get_json(next_url)
        if page is None:
            failed_page = True
            break
        page_items = page.get("orderedItems")
        if isinstance(page_items, list):
            flat_items.extend(e for e in page_items if isinstance(e, dict))
        nxt = page.get("next")
        if isinstance(nxt, str) and nxt:
            pages_to_walk.append(nxt)
        pages_walked += 1
    if pages_walked >= MAX_PAGES:
        logger.warning(
            f"list_sync: hit MAX_PAGES walking {inst.remote_id}; partial sync"
        )
    if failed_page:
        # A page in the chain failed to fetch. ``_sync_members_from_ap``
        # treats omitted entries as stale and would *delete* them, so a
        # transient failure on page 1 would empty the mirror and a
        # later-page failure would drop subsequent members. Retry without
        # mutating local state; the next attempt either succeeds or we
        # exhaust ``MAX_FETCH_ATTEMPTS`` and bail.
        _maybe_retry(class_path, pk, attempts)
        return
    pending = cls._sync_members_from_ap(inst, flat_items)
    if pending:
        # Items pending catalog fetch — retry so the next pass picks them
        # up after the catalog cache primes.
        _maybe_retry(class_path, pk, attempts)


def _maybe_retry(class_path: str, pk: int, attempts: int) -> None:
    if attempts + 1 >= MAX_FETCH_ATTEMPTS:
        return
    try:
        django_rq.get_queue("fetch").enqueue_in(
            RETRY_DELAY,
            "journal.jobs.list_sync.fetch_remote_list_members",
            class_path,
            pk,
            attempts + 1,
        )
    except Exception as e:
        logger.warning(f"list_sync: failed to reschedule {class_path}#{pk}: {e}")
