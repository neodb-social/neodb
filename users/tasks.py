from django.conf import settings
from .models import User
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
        logger.error(f"{user} mastodon data refresh failed")
