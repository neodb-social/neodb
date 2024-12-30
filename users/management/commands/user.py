from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from tqdm import tqdm

from users.models import Preference, User


class Command(BaseCommand):
    help = "Manage users"

    def add_arguments(self, parser):
        parser.add_argument("--list", action="store_true", help="list all users")
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--fix", action="store_true")
        parser.add_argument(
            "--integrity",
            action="store_true",
            help="check and fix integrity for missing data for user models",
        )
        parser.add_argument(
            "--super", action="store", nargs="*", help="list or toggle superuser"
        )
        parser.add_argument(
            "--staff", action="store", nargs="*", help="list or toggle staff"
        )
        parser.add_argument("--active", action="store", nargs="*", help="toggle active")

    def handle(self, *args, **options):
        self.verbose = options["verbose"]
        self.fix = options["fix"]
        self.users = User.objects.filter(is_active=True)
        if options["list"]:
            self.list(self.users)
        if options["integrity"]:
            self.integrity()
        if options["super"] is not None:
            self.superuser(options["super"])
        if options["staff"] is not None:
            self.staff(options["staff"])
        if options["active"]:
            self.set_active(options["active"])

    def list(self, users):
        for user in users:
            self.stdout.write(
                user.username.ljust(20)
                + str(user.date_joined.date()).ljust(12)
                + str(user.last_login.date() if user.last_login else "").ljust(12)
                + str(list(user.social_accounts.all())),
            )

    def integrity(self):
        count = 0
        for user in tqdm(User.objects.filter(is_active=True)):
            i = user.identity.takahe_identity
            if i.public_key is None:
                count += 1
                if self.fix:
                    i.generate_keypair()
            if i.inbox_uri is None:
                count += 1
                if self.fix:
                    i.ensure_uris()
            if not Preference.objects.filter(user=user).first():
                if self.fix:
                    Preference.objects.create(user=user)
                count += 1
        self.stdout.write(f"{count} issues")

    def superuser(self, v):
        if v == []:
            self.stdout.write("Super users:")
            self.list(self.users.filter(is_superuser=True))
        else:
            for n in v:
                u = User.objects.get(username=n, is_active=True)
                u.is_superuser = not u.is_superuser
                u.save()
                self.stdout.write(f"update {u} superuser: {u.is_superuser}")

    def staff(self, v):
        if v == []:
            self.stdout.write("Staff users:")
            self.list(self.users.filter(is_staff=True))
        else:
            for n in v:
                u = User.objects.get(username=n, is_active=True)
                u.is_staff = not u.is_staff
                u.save()
                self.stdout.write(f"update {u} staff: {u.is_staff}")

    def set_active(self, v):
        for n in v:
            u = User.objects.get(username=n)
            u.is_active = not u.is_active
            u.save()
            self.stdout.write(f"update {u} is_active: {u.is_active}")
