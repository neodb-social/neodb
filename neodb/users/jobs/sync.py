from datetime import timedelta

import django_rq
from django.db.models import F
from django.utils import timezone
from loguru import logger

from common.models import BaseJob, JobManager
from common.sentry import count as sentry_count
from users.models import User

_NON_EMAIL_ACCOUNT_TYPES = [
    "mastodon.mastodonaccount",
    "mastodon.threadsaccount",
    "mastodon.blueskyaccount",
]


@JobManager.register
class MastodonUserSync(BaseJob):
    interval_hours = 3

    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(hours=cls.interval_hours)

    def run(self):
        batches = (24 + self.interval_hours - 1) // self.interval_hours
        if batches < 1:
            batches = 1
        batch = timezone.now().hour // self.interval_hours
        logger.info(f"User accounts sync job starts batch {batch + 1} of {batches}")
        qs = (
            User.objects.exclude(
                preference__mastodon_skip_userinfo=True,
                preference__mastodon_skip_relationship=True,
            )
            .filter(
                username__isnull=False,
                is_active=True,
                social_accounts__type__in=_NON_EMAIL_ACCOUNT_TYPES,
            )
            .annotate(idmod=F("id") % batches)
            .filter(idmod=batch)
            .distinct()
            .values_list("id", flat=True)
        )
        queue = django_rq.get_queue("cron")
        dispatched = 0
        for user_id in qs.iterator():
            queue.enqueue(
                User.sync_accounts_task,
                user_id,
                sleep_hours=self.interval_hours,
                inactive_days=30,
                job_id=f"sync-user-{user_id}",
            )
            dispatched += 1
        sentry_count(
            "mastodon.usersync.dispatched",
            dispatched,
            attributes={"batch": str(batch)},
        )
        logger.info(
            f"User accounts sync dispatched {dispatched} users (batch {batch + 1} of {batches})"
        )
