from django.conf import settings
from django.core.management.base import BaseCommand
from loguru import logger
from tqdm import tqdm

from users.models import Preference, User


class Command(BaseCommand):
    help = "Manage import tasks"

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            count = 0
            for user in tqdm(User.objects.all()):
                if user.preference.import_status.get("douban_pending"):
                    user.preference.import_status["douban_pending"] = False
                    user.preference.save(update_fields=["import_status"])
                    count += 1
            self.stdout.write(self.style.SUCCESS(f"{count} users reset"))
