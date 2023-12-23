from django.conf import settings
from django.core.management.base import BaseCommand
from loguru import logger
from tqdm import tqdm

from takahe.utils import Takahe
from users.models import Preference, User


class Command(BaseCommand):
    help = "Manage import tasks"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
        )

    def handle(self, *args, **options):
        count = 0
        for user in tqdm(User.objects.all()):
            if (
                user.identity
                and not user.identity.takahe_identity.profile_uri.startswith("http")
            ):
                user.identity.takahe_identity.profile_uri = user.absolute_url
                user.identity.takahe_identity.save(update_fields=["profile_uri"])
                Takahe.update_state(user.identity.takahe_identity, "edited")
                count += 1
        self.stdout.write(self.style.SUCCESS(f"{count} user(s) fixed"))
