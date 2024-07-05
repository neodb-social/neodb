from datetime import timedelta
from enum import IntEnum

from django.db.models import F
from django.utils import timezone
from loguru import logger

from common.models import BaseJob, JobManager
from users.models import User


@JobManager.register
class MastodonUserSync(BaseJob):
    interval_hours = 3
    interval = timedelta(hours=interval_hours)

    def run(self):
        inactive_threshold = timezone.now() - timedelta(days=30)
        batches = (24 + self.interval_hours - 1) // self.interval_hours
        if batches < 1:
            batches = 1
        batch = timezone.now().hour // self.interval_hours
        logger.info(f"User accounts sync job starts batch {batch+1} of {batches}")
        qs = (
            User.objects.exclude(
                preference__mastodon_skip_userinfo=True,
                preference__mastodon_skip_relationship=True,
            )
            .filter(
                username__isnull=False,
                is_active=True,
            )
            .annotate(idmod=F("id") % batches)
            .filter(idmod=batch)
        )
        for user in qs.iterator():
            skip_graph = False
            if not user.last_login or user.last_login < inactive_threshold:
                last_usage = user.last_usage
                if not last_usage or last_usage < inactive_threshold:
                    skip_graph = True
            logger.debug(f"User accounts sync for {user}, skip_graph:{skip_graph}")
            user.sync_accounts(skip_graph, self.interval_hours)
        logger.info("User accounts sync job finished.")
