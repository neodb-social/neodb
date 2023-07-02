from django.core.management.base import BaseCommand
from users.models import User, Preference
from datetime import timedelta
from django.utils import timezone
from tqdm import tqdm


class Command(BaseCommand):
    help = "Check integrity all users"

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
        )
        parser.add_argument(
            "--fix",
            action="store_true",
        )
        parser.add_argument(
            "--integrity",
            action="store_true",
            help="check and fix integrity for merged and deleted items",
        )

    def handle(self, *args, **options):
        self.verbose = options["verbose"]
        self.fix = options["fix"]
        if options["integrity"]:
            self.integrity()

    def integrity(self):
        count = 0
        for user in tqdm(User.objects.all()):
            if not Preference.objects.filter(user=user).first():
                if self.fix:
                    Preference.objects.create(user=user)
                count += 1
        print(f"{count} missed preferences")
