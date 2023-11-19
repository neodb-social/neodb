import pprint
from datetime import timedelta
from time import sleep

from django.utils import timezone
from loguru import logger

from common.models import BaseJob, JobManager
from users.models import Preference, User


@JobManager.register
class MastodonUserSync(BaseJob):
    interval = timedelta(hours=2)

    def run(self):
        logger.info("Mastodon User Sync start.")
        count = 0
        ttl_hours = 12
        qs = (
            User.objects.exclude(
                preference__mastodon_skip_userinfo=True,
                preference__mastodon_skip_relationship=True,
            )
            .filter(
                mastodon_last_refresh__lt=timezone.now() - timedelta(hours=ttl_hours)
            )
            .filter(
                username__isnull=False,
                is_active=True,
            )
            .exclude(mastodon_token__isnull=True)
            .exclude(mastodon_token="")
        )
        for user in qs:
            user.refresh_mastodon_data()
        logger.info(f"Mastodon User Sync finished.")
