import datetime
import logging
from datetime import timedelta

from common.models import BaseJob, JobManager
from journal.models import WrappedDeck
from journal.wrapped import (
    can_make,
    compute_instance_stats,
    compute_kindred,
    latest_year,
    refresh_deck,
    year_aggregates,
)

logger = logging.getLogger(__name__)


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
    """Store the card data again for every deck whose member left the daily update on, while updates are
    open (1 December to the end of February). One deck after another, each written only when its data
    changed; a deck that fails is logged and the others go on."""

    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(days=1)

    def run(self):
        years = [y for y in (latest_year() - 1, latest_year()) if can_make(y)]
        if not years:
            return
        ids = list(
            WrappedDeck.objects.filter(
                year__in=years, daily=True, refreshed_time__isnull=False
            ).values_list("pk", flat=True)
        )
        changed = failed = 0
        # the instance totals and kindred matches are the same for every deck of a year: read them once
        aggregates = {y: year_aggregates(y) for y in years}
        for pk in ids:
            deck = WrappedDeck.objects.filter(pk=pk, daily=True).first()
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
