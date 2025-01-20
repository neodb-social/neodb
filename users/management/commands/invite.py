from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse

from takahe.utils import Invite


class Command(BaseCommand):
    help = "Manage invite"

    def add_arguments(self, parser):
        parser.add_argument(
            "--create",
            action="store_true",
        )
        # parser.add_argument(
        #     "--revoke",
        #     action="store_true",
        # )

    def handle(self, *args, **options):
        if options["create"]:
            inv = Invite.create_random()
            self.stdout.write(self.style.SUCCESS(f"Invite created: {inv.token}"))
            self.stdout.write(
                self.style.SUCCESS(
                    f"Link: {settings.SITE_INFO['site_url']}{reverse('users:login')}?invite={inv.token}"
                )
            )
