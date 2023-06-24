from django.utils.translation import gettext_lazy as _
from .models import User
from mastodon.api import *
from common.config import *
from django.utils import timezone
from datetime import timedelta
from django.conf import settings


def refresh_mastodon_data_task(user, token=None):
    if token:
        user.mastodon_token = token
    if user.refresh_mastodon_data():
        user.save()
        print(f"{user} mastodon data refreshed")
    else:
        print(f"{user} mastodon data refresh failed")


def refresh_all_users_mastodon_data_task():
        for user in User.objects.filter(
            mastodon_last_refresh__lt=timezone.now() - timedelta(hours=settings.MASTODON_DATA_TTL),
            is_active=True,
        ):
            if user.mastodon_token or user.mastodon_refresh_token:
                print(f"Refreshing {user}")
                if user.refresh_mastodon_data():
                    print(f"Refreshed {user}")
                    count += 1
                else:
                    print(f"Refresh failed for {user}")
                user.save()
            else:
                print(f"Missing token for {user}")
