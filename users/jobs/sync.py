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
        logger.info("Mastodon User Sync start.")
        inactive_threshold = timezone.now() - timedelta(days=90)
        batch = (24 + self.interval_hours - 1) // self.interval_hours
        if batch < 1:
            batch = 1
        m = timezone.now().hour // self.interval_hours
        qs = (
            User.objects.exclude(
                preference__mastodon_skip_userinfo=True,
                preference__mastodon_skip_relationship=True,
            )
            .filter(
                username__isnull=False,
                is_active=True,
            )
            .annotate(idmod=F("id") % batch)
            .filter(idmod=m)
        )
        for user in qs.iterator():
            skip_detail = False
            if not user.last_login or user.last_login < inactive_threshold:
                last_usage = user.last_usage
                if not last_usage or last_usage < inactive_threshold:
                    logger.info(f"Skip {user} detail because of inactivity.")
                    skip_detail = True
            user.refresh_mastodon_data(skip_detail, self.interval_hours)
        logger.info("Mastodon User Sync finished.")
