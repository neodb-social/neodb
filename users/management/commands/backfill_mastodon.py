from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand

from users.models import User


class Command(BaseCommand):
    help = "Backfill Mastodon data if missing"

    def handle(self, *args, **options):
        for session in Session.objects.order_by("-expire_date"):
            uid = session.get_decoded().get("_auth_user_id")
            token = session.get_decoded().get("oauth_token")
            if uid and token:
                user = User.objects.get(pk=uid)
                if user.mastodon_token:
                    print(f"skip {user}")
                    continue
                user.mastodon_token = token
                user.refresh_mastodon_data()
                user.save()
                print(f"Refreshed {user}")
