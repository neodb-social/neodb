from loguru import logger

from .models import User


def refresh_mastodon_data_task(user_id):
    user = User.objects.get(pk=user_id)
    if not user.mastodon:
        logger.info(f"{user} mastodon data refresh skipped")
        return
    if user.refresh_mastodon_data():
        logger.info(f"{user} mastodon data refreshed")
    else:
        logger.warning(f"{user} mastodon data refresh failed")
