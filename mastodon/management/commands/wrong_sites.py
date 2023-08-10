from django.conf import settings
from django.core.management.base import BaseCommand

from mastodon.api import get_instance_info
from mastodon.models import MastodonApplication
from users.models import User


class Command(BaseCommand):
    help = "Find wrong sites"

    def handle(self, *args, **options):
        for site in MastodonApplication.objects.all():
            d = site.domain_name
            login_domain = (
                d.strip().lower().split("//")[-1].split("/")[0].split("@")[-1]
            )
            domain, version = get_instance_info(login_domain)
            if d != domain:
                print(f"{d} should be {domain}")
                for u in User.objects.filter(mastodon_site=d, is_active=True):
                    u.mastodon_site = domain
                    print(f"fixing {u}")
                    u.save()
