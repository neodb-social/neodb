import datetime
import logging
from datetime import timedelta

from common.models import BaseJob, JobManager
from journal.wrapped import compute_instance_stats, compute_kindred

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
