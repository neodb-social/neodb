from django.core.management.base import BaseCommand
from users.models import User
from datetime import timedelta
from django.utils import timezone
from tqdm import tqdm


class Command(BaseCommand):
    help = "Refresh Mastodon data for all users if not updated in last 24h"

    def handle(self, *args, **options):
        count = 0
        for user in tqdm(
            User.objects.filter(
                mastodon_last_refresh__lt=timezone.now() - timedelta(hours=24),
                is_active=True,
            )
        ):
            if user.mastodon_token or user.mastodon_refresh_token:
                tqdm.write(f"Refreshing {user}")
                if user.refresh_mastodon_data():
                    tqdm.write(f"Refreshed {user}")
                    count += 1
                else:
                    tqdm.write(f"Refresh failed for {user}")
                user.save()
            else:
                tqdm.write(f"Missing token for {user}")

        print(f"{count} users updated")
