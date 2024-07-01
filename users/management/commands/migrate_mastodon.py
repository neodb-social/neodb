from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from tqdm import tqdm

from catalog.common import jsondata
from mastodon.models import Email, MastodonAccount, mastodon
from mastodon.models.email import EmailAccount
from users.models import Preference, User


class Command(BaseCommand):
    def handle(self, *args, **options):
        m = 0
        e = 0
        for user in tqdm(User.objects.filter(is_active=True)):
            if user.mastodon_username:
                MastodonAccount.objects.update_or_create(
                    handle=f"{user.mastodon_username}@{user.mastodon_site}",
                    defaults={
                        "user": user,
                        "uid": user.mastodon_id,
                        "domain": user.mastodon_site,
                        "created": user.date_joined,
                        "last_refresh": user.mastodon_last_refresh,
                        "last_reachable": user.mastodon_last_reachable,
                        "followers": user.mastodon_followers,
                        "following": user.mastodon_following,
                        "blocks": user.mastodon_blocks,
                        "mutes": user.mastodon_mutes,
                        "domain_blocks": user.mastodon_domain_blocks,
                        "account_data": user.mastodon_account,
                        "access_data": {
                            "access_token": jsondata.encrypt_str(user.mastodon_token)
                        },
                    },
                )
                m += 1
            if user.email:
                EmailAccount.objects.update_or_create(
                    handle=user.email,
                    defaults={
                        "user": user,
                        "uid": user.email.split("@")[0],
                        "domain": user.email.split("@")[1],
                        "created": user.date_joined,
                    },
                )
                e += 1
        print(f"{m} Mastodon, {e} Email migrated.")
