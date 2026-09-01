from datetime import timedelta

from loguru import logger

from common.durable_work import recover_expired_claims
from common.models import BaseJob, JobManager


@JobManager.register
class DurableDispatchSweeper(BaseJob):
    """Recover abandoned delivery leases through the existing cron queue."""

    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(minutes=1)

    def run(self):
        recovered = recover_expired_claims()
        if recovered:
            logger.warning(
                f"Recovered {recovered} durable dispatch claim(s) for observation."
            )
