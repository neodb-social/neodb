from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from tqdm import tqdm

from users.models import User
from users.models.user import _RESERVED_USERNAMES


class Command(BaseCommand):
    help = "Generate unique username"

    def handle(self, *args, **options):
        count = 0
        for user in User.objects.filter(username__isnull=True).order_by("date_joined"):
            if not user.is_active:
                un = f"-{user.pk}-"
            else:
                un = user.mastodon_username
            if not un:
                un = f"_{user.pk}"
            if un.lower() in _RESERVED_USERNAMES:
                un = f"__{un}"
            if User.objects.filter(username__iexact=un).exists():
                un = f"{un}_{user.pk}"
            print(f"{user} -> {un}")
            user.username = un
            user.save(update_fields=["username"])
            count += 1
        print(f"{count} users updated")
