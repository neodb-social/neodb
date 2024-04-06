import pprint

from django.core.management.base import BaseCommand

from mastodon.api import create_app
from mastodon.models import MastodonApplication


class Command(BaseCommand):
    help = "Manage Mastodon sites"

    def add_arguments(self, parser):
        #     parser.add_argument("domain", type=str, help="mastodon domain")
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="refresh app registration on all sites",
        )

    def handle(self, *args, **options):
        if options["refresh"]:
            for site in MastodonApplication.objects.exclude(disabled=True):
                allow_multiple_redir = True
                response = create_app(site.api_domain, allow_multiple_redir)
                if response.status_code != 200:
                    self.stdout.write(
                        f"Error creating app on {site.api_domain}: {response.status_code}"
                    )
                    continue
                try:
                    data = response.json()
                except Exception:
                    self.stdout.write(
                        f"Error creating app on {site.api_domain}: unable to parse response"
                    )
                    continue
                site.app_id = data["id"]
                site.client_id = data["client_id"]
                site.client_secret = data["client_secret"]
                site.vapid_key = data.get("vapid_key")
                site.save(
                    update_fields=["app_id", "client_id", "client_secret", "vapid_key"]
                )
                self.stdout.write(f"updated app on {site.api_domain}")
        self.stdout.write(self.style.SUCCESS("Done."))
