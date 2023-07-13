from django.conf import settings
from .models import User
from datetime import timedelta
from django.utils import timezone
from tqdm import tqdm
from loguru import logger


def refresh_mastodon_data_task(user_id, token=None):
    user = User.objects.get(pk=user_id)
    if not user.mastodon_username:
        logger.info(f"{user} mastodon data refresh skipped")
        return
    if token:
        user.mastodon_token = token
    if user.refresh_mastodon_data():
        user.save()
        logger.info(f"{user} mastodon data refreshed")
    else:
        logger.warning(f"{user} mastodon data refresh failed")


def refresh_all_mastodon_data_task(ttl_hours):
    logger.info(f"Mastodon data refresh start")
    count = 0
    for user in tqdm(
        User.objects.filter(
            mastodon_last_refresh__lt=timezone.now() - timedelta(hours=ttl_hours),
            is_active=True,
        )
    ):
        if user.mastodon_token or user.mastodon_refresh_token:
            logger.info(f"Refreshing {user}")
            if user.refresh_mastodon_data():
                logger.info(f"Refreshed {user}")
                count += 1
            else:
                logger.warning(f"Refresh failed for {user}")
            user.save()
        else:
            logger.warning(f"Missing token for {user}")
    logger.info(f"{count} users updated")
    c = User.merge_rejected_by()
    logger.info(f"{c} users's rejecting list updated")
    logger.info(f"Mastodon data refresh done")
