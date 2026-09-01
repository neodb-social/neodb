from datetime import timedelta

from loguru import logger

from common.models import BaseJob, JobManager
from users.managed_community import (
    reconcile_managed_community_dispatches,
    reconcile_managed_community_observations,
)


@JobManager.register
class ManagedCommunityReconciler(BaseJob):
    """Sweep only managed Community durable responsibilities."""

    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(minutes=1)

    def run(self):
        observations = reconcile_managed_community_observations()
        result = reconcile_managed_community_dispatches()
        if observations or result.claimed:
            logger.info(
                "Managed Community reconciliation: "
                f"observed={observations} claimed={result.claimed} "
                f"dispatched={result.dispatched} enqueue_errors={result.enqueue_errors}"
            )
