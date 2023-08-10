from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from tqdm import tqdm

from users.models import User


class Command(BaseCommand):
    help = "Refresh following data for all users"

    def handle(self, *args, **options):
        count = 0
        for user in tqdm(User.objects.all()):
            user.following = user.merge_following_ids()
            if user.following:
                count += 1
                user.save(update_fields=["following"])

        print(f"{count} users updated")
