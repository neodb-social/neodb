from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from users.models import User


class Command(BaseCommand):
    help = "disable user"

    def add_arguments(self, parser):
        parser.add_argument("id", type=int, help="user id")

    def handle(self, *args, **options):
        h = int(options["id"])
        u = User.objects.get(pk=h)
        u.is_active = False
        u.save()
        print(f"{u} disabled")
