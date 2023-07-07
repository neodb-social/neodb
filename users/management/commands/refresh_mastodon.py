from django.core.management.base import BaseCommand
from users.tasks import refresh_all_mastodon_data_task


class Command(BaseCommand):
    help = "Refresh Mastodon data for all users if not updated in last 24h"

    def handle(self, *args, **options):
        refresh_all_mastodon_data_task(24)
