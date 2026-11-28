import datetime
import logging
import uuid
from datetime import timedelta

import django_rq
from django.core.cache import cache

from common.models import BaseJob, JobManager
from journal.models import WrappedDeck
from journal.wrapped.aggregates import (
    compute_instance_stats,
    compute_kindred,
    year_aggregates,
)
from journal.wrapped.record import (
    auto_update_open,
    latest_year,
    refresh_deck,
)

logger = logging.getLogger(__name__)

# a member's deck is made and updated by hand in a job, as an update reads the whole year three times; the marker
# below keeps one job a deck queued or running, and tells the owner's page that the cards are on their way. It holds
# its job's token, so only that job clears it; it also expires, so a worker killed mid-job cannot leave it for good
REFRESH_QUEUE = "export"
REFRESH_PENDING_SECONDS = 600
REFRESH_FAILED_SECONDS = 600


def _pending_key(deck_pk: int) -> str:
    return f"wrapped:updating:{deck_pk}"


def _failed_key(deck_pk: int) -> str:
    return f"wrapped:update-failed:{deck_pk}"


def refresh_state(deck_pk: int) -> str:
    """ "updating" while a job for the deck is queued or running, "failed" for a while after one failed, else empty."""
    if cache.get(_pending_key(deck_pk)):
        return "updating"
    return "failed" if cache.get(_failed_key(deck_pk)) else ""


def enqueue_deck_refresh(deck: WrappedDeck, make: bool = False) -> bool:
    """Queue a job that stores the deck's card data again, unless one is queued or running; True when this
    call queued it. ``make``: the job is the step that makes the deck, and does nothing once it is made."""
    token = uuid.uuid4().hex
    if not cache.add(_pending_key(deck.pk), token, REFRESH_PENDING_SECONDS):
        return False
    cache.delete(_failed_key(deck.pk))
    try:
        django_rq.get_queue(REFRESH_QUEUE).enqueue(
            refresh_deck_job, deck.pk, make, token
        )
    except Exception:
        _release(deck.pk, token)
        raise
    return True


def _release(deck_pk: int, token: str) -> None:
    # a job queued after this marker expired holds a marker of its own
    if cache.get(_pending_key(deck_pk)) == token:
        cache.delete(_pending_key(deck_pk))


def refresh_deck_job(deck_pk: int, make: bool = False, token: str = "") -> None:
    key = _pending_key(deck_pk)
    # the marker counts from the enqueue and may have expired while the job waited in the queue: hold it again
    # for the run, unless a later job holds it
    if cache.get(key) in (None, token):
        cache.set(key, token, REFRESH_PENDING_SECONDS)
    try:
        deck = WrappedDeck.objects.filter(pk=deck_pk).first()
        # the owner deleted the deck meanwhile, or a job before this one made it
        if deck is None or (make and deck.refreshed_time is not None):
            return
        refresh_deck(deck)
    except Exception:
        # before the marker goes, so the waiting page never sees a job that ended without a result; not while a
        # later job holds the marker, as the failure is not that job's
        if cache.get(key) == token:
            cache.set(_failed_key(deck_pk), 1, REFRESH_FAILED_SECONDS)
        raise
    finally:
        _release(deck_pk, token)


@JobManager.register
class WrappedInstanceStats(BaseJob):
    """Refresh the instance totals of the annual summary for this year and the last.

    The cache outlives the interval, so a page never waits for the aggregate; the job also
    keeps a member's saved list of shown cards from losing the cards that need these totals.
    """

    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(hours=6)

    def run(self):
        this = datetime.date.today().year
        for year in (this - 1, this):
            stats = compute_instance_stats(year)
            logger.info(
                f"wrapped instance stats {year}: {len(stats['counts'])} members"
            )


@JobManager.register
class WrappedKindred(BaseJob):
    """Refresh the kindred matches of the annual summary for this year and the last.

    A whole-instance batch over public completions (2026 on neodb.social: about 200,000 marks and
    2 million additions, a few seconds); the cache outlives the daily interval.
    """

    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(days=1)

    def run(self):
        this = datetime.date.today().year
        for year in (this - 1, this):
            matches = compute_kindred(year)
            logger.info(f"wrapped kindred {year}: {len(matches)} members matched")


@JobManager.register
class WrappedDailyUpdate(BaseJob):
    """Store the card data again for every made deck of a year, every day in December, the last
    run on 1 January (the member can still update it by hand to the end of January). One deck after another, each written
    only when its data changed; a deck that fails is logged and the others go on."""

    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(days=1)

    def run(self):
        years = [y for y in (latest_year() - 1, latest_year()) if auto_update_open(y)]
        if not years:
            return
        ids = list(
            WrappedDeck.objects.filter(
                year__in=years, refreshed_time__isnull=False
            ).values_list("pk", flat=True)
        )
        changed = failed = 0
        # the instance totals and kindred matches are the same for every deck of a year: read them once
        aggregates = {y: year_aggregates(y) for y in years}
        for pk in ids:
            deck = WrappedDeck.objects.filter(pk=pk).first()
            if deck is None:
                continue
            try:
                changed += refresh_deck(deck, aggregates[deck.year])
            except Exception:
                failed += 1
                logger.exception(f"wrapped daily update of deck {pk} failed")
        logger.info(
            f"wrapped daily update {years}: {len(ids)} decks, {changed} changed, {failed} failed"
        )
