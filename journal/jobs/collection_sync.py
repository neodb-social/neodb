from datetime import timedelta

import django_rq
from loguru import logger

from journal.models import Collection
from takahe.auth import sign_get

MAX_FETCH_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=5)


def fetch_remote_collection_members(collection_pk: int, attempts: int = 0) -> None:
    """Fetch the full Collection AP object from its origin and (re)materialize
    members on the local mirror.

    The announcement Note Post only carries Collection metadata; the
    ``orderedItems`` list is served by the dereferenceable AP endpoint at
    ``Collection.remote_id`` and requires a signed GET. This job runs that
    fetch, parses ``orderedItems``, and feeds them through
    ``Collection._sync_members_from_ap`` to upsert ``CollectionMember`` rows.

    Retries on transient errors up to ``MAX_FETCH_ATTEMPTS``.
    """
    col = Collection.objects.filter(pk=collection_pk, local=False).first()
    if not col or not col.remote_id:
        logger.debug(
            f"fetch_remote_collection_members: {collection_pk} gone or no remote_id"
        )
        return
    try:
        response = sign_get(col.remote_id)
    except Exception as e:
        logger.warning(
            f"fetch_remote_collection_members: signed GET to {col.remote_id} failed: {e}"
        )
        _maybe_retry(col, attempts)
        return
    if response.status_code == 404:
        logger.info(
            f"fetch_remote_collection_members: {col.remote_id} returned 404; "
            "leaving member list unchanged"
        )
        return
    if response.status_code != 200:
        logger.warning(
            f"fetch_remote_collection_members: {col.remote_id} returned "
            f"{response.status_code}"
        )
        _maybe_retry(col, attempts)
        return
    try:
        ap_obj = response.json()
    except ValueError as e:
        logger.warning(
            f"fetch_remote_collection_members: bad JSON from {col.remote_id}: {e}"
        )
        return
    ordered_items = ap_obj.get("orderedItems") or []
    pending = Collection._sync_members_from_ap(col, ordered_items)
    if pending:
        # Some items had to be queued for catalog fetch; retry the signed GET
        # later so the next pass can finish materializing the member list.
        _maybe_retry(col, attempts)


def _maybe_retry(col: Collection, attempts: int) -> None:
    if attempts + 1 >= MAX_FETCH_ATTEMPTS:
        return
    try:
        django_rq.get_queue("fetch").enqueue_in(
            RETRY_DELAY,
            "journal.jobs.collection_sync.fetch_remote_collection_members",
            col.pk,
            attempts + 1,
        )
    except Exception as e:
        logger.warning(
            f"fetch_remote_collection_members: failed to reschedule {col.pk}: {e}"
        )
