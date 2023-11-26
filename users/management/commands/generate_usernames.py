from datetime import timedelta

from django.apps import apps
from django.core.management.base import BaseCommand
from django.utils import timezone
from tqdm import tqdm

from users.models.user import _RESERVED_USERNAMES

User = apps.get_model("users", "User")
_RESERVED_USERNAMES = [
    "connect",
    "oauth2_login",
    "__",
    "admin",
    "api",
    "me",
]


class Command(BaseCommand):
    help = "Generate unique username"

    def process_users(self, users):
        count = 0
        for user in users:
            if not user.is_active:
                un = f"-{user.pk}-"
            else:
                un = user.mastodon_username
            if not un:
                un = f"_{user.pk}"
            if un.lower() in _RESERVED_USERNAMES:
                un = f"__{un}"
            if User.objects.filter(username__iexact=un).exists():  # type: ignore
                un = f"{un}_{user.pk}"
            print(f"{user} -> {un}")
            user.username = un
            user.save(update_fields=["username"])
            count += 1
        print(f"{count} users updated")

    def handle(self, *args, **options):
        print("Processing active users")
        # recent logged in users
        proactive_users = User.objects.filter(  # type: ignore
            username__isnull=True,
            is_active=True,
            last_login__gt=timezone.now() - timedelta(days=30),
        ).order_by("date_joined")
        # users with mastodon token still active
        active_users = (
            User.objects.filter(  # type: ignore
                username__isnull=True,
                is_active=True,
            )
            .exclude(mastodon_token="")
            .order_by("date_joined")
        )
        # users with mastodon handler still reachable
        reachable_users = User.objects.filter(  # type: ignore
            username__isnull=True,
            is_active=True,
            mastodon_last_reachable__gt=timezone.now() - timedelta(days=7),
        ).order_by("date_joined")
        # all other users
        users = User.objects.filter(  # type: ignore
            username__isnull=True,
        ).order_by("date_joined")
        print(f"{proactive_users.count()} proactive users")
        self.process_users(proactive_users)
        print(f"{active_users.count()} active users")
        self.process_users(active_users)
        print(f"{reachable_users.count()} reachable users")
        self.process_users(reachable_users)
        print(f"{users.count()} other users")
        self.process_users(users)
