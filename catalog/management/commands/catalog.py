from django.core.management.base import BaseCommand
from django.db.models import Count, F
import pprint
from catalog.models import *
from journal.models import update_journal_for_merged_item


class Command(BaseCommand):
    help = "catalog app utilities"

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
            help="purge deleted items",
        )
        parser.add_argument(
            "--integrity",
            action="store_true",
            help="check and fix integrity for merged and deleted items",
        )
        parser.add_argument(
            "--journal",
            action="store_true",
            help="check and fix remaining journal for merged and deleted items",
        )

    def handle(self, *args, **options):
        self.verbose = options["verbose"]
        self.fix = options["fix"]
        if options["purge"]:
            self.purge()
        if options["integrity"]:
            self.integrity()
        if options["journal"]:
            self.journal()
        self.stdout.write(self.style.SUCCESS(f"Done."))

    def purge(self):
        for cls in Item.__subclasses__():
            if self.fix:
                self.stdout.write(f"Cleaning up {cls}...")
                cls.objects.filter(is_deleted=True).delete()

    def integrity(self):
        self.stdout.write(f"Checking circulated merge...")
        for i in Item.objects.filter(merged_to_item=F("id")):
            self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")
            if self.fix:
                i.merged_to_item = None
                i.save()

        self.stdout.write(f"Checking chained merge...")
        for i in (
            Item.objects.filter(merged_to_item__isnull=False)
            .annotate(n=Count("merged_from_items"))
            .exclude(n=0)
        ):
            self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")
            if self.fix:
                for j in i.merged_from_items.all():
                    j.merged_to_item = i.merged_to_item
                    j.save()

        self.stdout.write(f"Checking deleted merge...")
        for i in Item.objects.filter(merged_to_item__isnull=False, is_deleted=True):
            self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")
            if self.fix:
                i.is_deleted = False
                i.save()

        self.stdout.write(f"Checking deleted item with external resources...")
        for i in (
            Item.objects.filter(is_deleted=True)
            .annotate(n=Count("external_resources"))
            .exclude(n=0)
        ):
            self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")
            if self.fix:
                for r in i.external_resources.all():
                    r.item = None
                    r.save()

        self.stdout.write(f"Checking merged item with external resources...")
        for i in (
            Item.objects.filter(merged_to_item__isnull=False)
            .annotate(n=Count("external_resources"))
            .exclude(n=0)
        ):
            self.stdout.write(f"! {i} : {i.absolute_url}?skipcheck=1")
            if self.fix:
                for r in i.external_resources.all():
                    r.item = i.merged_to_item
                    r.save()

    def journal(self):
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
