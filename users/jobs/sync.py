from datetime import timedelta

from django.utils import timezone
from loguru import logger

from common.models import BaseJob, JobManager
from users.models import User


@JobManager.register
class MastodonUserSync(BaseJob):
    batch = 16
    interval_hours = 3
    interval = timedelta(hours=interval_hours)

    def run(self):
        logger.info("Mastodon User Sync start.")
        inactive_threshold = timezone.now() - timedelta(days=60)
        qs = (
            User.objects.exclude(
                preference__mastodon_skip_userinfo=True,
                preference__mastodon_skip_relationship=True,
            )
            .filter(
                mastodon_last_refresh__lt=timezone.now()
                - timedelta(hours=self.interval_hours * self.batch)
            )
            .filter(
                username__isnull=False,
                is_active=True,
            )
            .exclude(mastodon_token__isnull=True)
            .exclude(mastodon_token="")
        )
        for user in qs.iterator():
            if user.last_login < inactive_threshold:
                last_usage = user.last_usage
                if not last_usage or last_usage < inactive_threshold:
                    logger.warning(f"Skip {user} because of inactivity.")
                    continue
            user.refresh_mastodon_data()
        logger.info(f"Mastodon User Sync finished.")
