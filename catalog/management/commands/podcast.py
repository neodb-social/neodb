from django.core.management.base import BaseCommand
from django.conf import settings
from catalog.common.models import IdType
from catalog.models import *
from catalog.sites import RSS
import pprint
from django.core.paginator import Paginator
from tqdm import tqdm
from time import sleep
from datetime import timedelta
from django.utils import timezone


class Command(BaseCommand):
    help = "Manage podcast data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--update",
            help="update latest episode",
            action="store_true",
        )
        parser.add_argument(
            "--stat",
            action="store_true",
        )

    def stat(self, *args, **options):
        qs = Podcast.objects.filter(is_deleted=False, merged_to_item__isnull=True)
        for p in qs.order_by("pk"):
            self.stdout.write(f"{p.episodes.count()}\t{p.title}\n")

    def update(self):
        qs = Podcast.objects.filter(is_deleted=False, merged_to_item__isnull=True)
        for p in tqdm(qs.order_by("pk")):
            if (
                p.primary_lookup_id_type == IdType.RSS
                and p.primary_lookup_id_value is not None
            ):
                site = RSS(p.feed_url)
                site.scrape_additional_data()
        self.stdout.write(self.style.SUCCESS("Podcasts updated."))

    def handle(self, *args, **options):
        if options["update"]:
            self.update()
        if options["stat"]:
            self.stat()
