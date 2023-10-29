from django.core.management.base import BaseCommand

from catalog.models import Item
from journal.importers.douban import DoubanImporter
from journal.models import *
from journal.models.common import Content
from journal.models.itemlist import ListMember
from users.models import User


class Command(BaseCommand):
    help = "journal app utilities"

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
            "--purge",
            action="store_true",
            help="purge invalid data (visibility=99)",
        )
        parser.add_argument(
            "--douban-import-redo",
            action="store",
            help="reimport for user id",
        )
        parser.add_argument(
            "--douban-import-reset",
            action="store",
            help="reset for user id",
        )
        parser.add_argument(
            "--integrity",
            action="store_true",
            help="check and fix remaining journal for merged and deleted items",
        )

    def integrity(self):
        self.stdout.write(f"Checking deleted items with remaining journals...")
        for i in Item.objects.filter(is_deleted=True):
            if i.journal_exists():
                self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")

        self.stdout.write(f"Checking merged items with remaining journals...")
        for i in Item.objects.filter(merged_to_item__isnull=False):
            if i.journal_exists():
                self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")
                if self.fix:
                    update_journal_for_merged_item(i.url)

    def handle(self, *args, **options):
        self.verbose = options["verbose"]
        self.fix = options["fix"]
        if options["integrity"]:
            self.integrity()

        if options["purge"]:
            for pcls in [Content, ListMember]:
                for cls in pcls.__subclasses__():
                    self.stdout.write(f"Cleaning up {cls}...")
                    cls.objects.filter(visibility=99).delete()

        if options["douban_import_redo"]:
            user = User.objects.get(pk=options["douban_import_redo"])
            self.stdout.write(f"Redo import for {user}...")
            DoubanImporter.redo(user)

        if options["douban_import_reset"]:
            user = User.objects.get(pk=options["douban_import_reset"])
            self.stdout.write(f"Reset import for {user}...")
            DoubanImporter.reset(user)

        self.stdout.write(self.style.SUCCESS(f"Done."))
