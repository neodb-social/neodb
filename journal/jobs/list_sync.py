"""Page-walking inbound sync for federated NeoDB List objects.

Used for both NeoDB ``Collection`` and NeoDB ``Shelf`` mirrors. Given a
local ``List`` mirror plus the ``items_url`` captured from the inbound
envelope (``first`` or ``items``), this job:

1. Walks the ``items_url``→``next`` chain of ``OrderedCollectionPage``
   URLs, accumulating ``orderedItems`` entries.
2. Hands the flattened list to ``cls._sync_members_from_ap`` to persist.

The envelope is parsed *inline* from the announcement Post by
``update_by_ap_envelope``; we don't re-dereference the envelope ``id``
here. This mirrors how Review consumes its inline ``relatedWith`` and
removes the requirement that the envelope ``id`` self-resolve to AP —
peers only need the items endpoint (``/collection/<uuid>/items``) to
remain AP-dereferenceable, which it is.

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


def fetch_remote_list_members(
    class_path: str,
    pk: int,
    items_url: str | None = None,
    inline_items: list[dict[str, Any]] | None = None,
    attempts: int = 0,
) -> None:
    """Resync members for one local ``List`` mirror.

    ``class_path`` is the dotted import path of the model class (e.g.
    ``journal.models.collection.Collection`` or
    ``journal.models.shelf.Shelf``) so the same job module serves all
    federated list types.

    ``items_url`` is the starting page URL — the ``first`` (or
    ``items``) field captured from the inbound envelope. ``None`` if
    the envelope had no paginated items endpoint (e.g. a server that
    inlines ``orderedItems`` instead).

    ``inline_items`` is the envelope's own ``orderedItems`` list (if
    any) — the original payload may carry the first slice inline AND
    a ``first`` URL to the rest, so both inputs flow through the same
    ``_sync_members_from_ap`` call. Both default to ``None`` for
    backward compatibility with jobs queued under the previous
    signature.
    """
    cls = _resolve_class(class_path)
    inst = cls.objects.filter(pk=pk, local=False).first()
    if not inst:
        logger.debug(f"list_sync: {class_path}#{pk} gone")
        return
    flat_items: list[dict[str, Any]] = [
        e for e in (inline_items or []) if isinstance(e, dict)
    ]
    failed_page = False
    pages_walked = 0
    if items_url:
        pages_to_walk: list[str] = [items_url]
        visited: set[str] = set()
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
                f"list_sync: hit MAX_PAGES walking {items_url}; partial sync"
            )
    if failed_page:
        # A page in the chain failed to fetch. ``_sync_members_from_ap``
        # treats omitted entries as stale and would *delete* them, so a
        # transient failure on page 1 would empty the mirror and a
        # later-page failure would drop subsequent members. Retry without
        # mutating local state; the next attempt either succeeds or we
        # exhaust ``MAX_FETCH_ATTEMPTS`` and bail.
        _maybe_retry(class_path, pk, items_url, inline_items, attempts)
        return
    if not flat_items and not items_url:
        # Nothing to do — neither inline items nor a paginated URL.
        return
    pending = cls._sync_members_from_ap(inst, flat_items)
    if pending:
        # Items pending catalog fetch — retry so the next pass picks them
        # up after the catalog cache primes.
        _maybe_retry(class_path, pk, items_url, inline_items, attempts)


def _maybe_retry(
    class_path: str,
    pk: int,
    items_url: str | None,
    inline_items: list[dict[str, Any]] | None,
    attempts: int,
) -> None:
    if attempts + 1 >= MAX_FETCH_ATTEMPTS:
        return
    try:
        django_rq.get_queue("fetch").enqueue_in(
            RETRY_DELAY,
            "journal.jobs.list_sync.fetch_remote_list_members",
            class_path,
            pk,
            items_url,
            inline_items,
            attempts + 1,
        )
    except Exception as e:
        logger.warning(f"list_sync: failed to reschedule {class_path}#{pk}: {e}")
