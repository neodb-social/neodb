import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import partial

from django.db import close_old_connections, connections
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

MAX_SYNC_WORKERS = 8


def _sync_one(user_id: int, sleep_hours: int) -> bool:
    try:
        User.sync_accounts_task(user_id, sleep_hours=sleep_hours, inactive_days=30)
        return True
    except Exception as e:
        logger.warning(f"user {user_id} accounts sync failed: {e}")
        return False
    finally:
        # Django connections are thread-local; close before the pool reuses
        # the thread to avoid connection leaks.
        connections.close_all()


@JobManager.register
class MastodonUserSync(BaseJob):
    interval_hours = 3

    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(hours=cls.interval_hours)

    def run(self):
        start = time.time()
        batches = (24 + self.interval_hours - 1) // self.interval_hours
        if batches < 1:
            batches = 1
        batch = timezone.now().hour // self.interval_hours
        logger.info(f"User accounts sync job starts batch {batch + 1} of {batches}")
        user_ids = list(
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
        failed = 0
        if user_ids:
            with ThreadPoolExecutor(max_workers=MAX_SYNC_WORKERS) as ex:
                for ok in ex.map(
                    partial(_sync_one, sleep_hours=self.interval_hours), user_ids
                ):
                    if not ok:
                        failed += 1
            close_old_connections()
        sentry_count(
            "mastodon.usersync.processed",
            len(user_ids),
            attributes={"batch": str(batch)},
        )
        if failed:
            sentry_count(
                "mastodon.usersync.failed",
                failed,
                attributes={"batch": str(batch)},
            )
        t = round(time.time() - start, 1)
        logger.info(
            f"User accounts sync finished in {t}s: "
            f"{len(user_ids)} users, {failed} failed (batch {batch + 1} of {batches})"
        )
