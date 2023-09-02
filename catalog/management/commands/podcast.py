import pprint
from datetime import timedelta
from time import sleep

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.paginator import Paginator
from django.utils import timezone
from loguru import logger
from tqdm import tqdm

from catalog.common.models import IdType
from catalog.models import *
from catalog.sites import RSS


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
        logger.info("Podcasts update start.")
        count = 0
        qs = Podcast.objects.filter(is_deleted=False, merged_to_item__isnull=True)
        for p in tqdm(qs.order_by("pk")):
            if (
                p.primary_lookup_id_type == IdType.RSS
                and p.primary_lookup_id_value is not None
            ):
                logger.info(f"updating {p}")
                c = p.episodes.count()
                site = RSS(p.feed_url)
                site.scrape_additional_data()
                c2 = p.episodes.count()
                logger.info(f"updated {p}, {c2-c} new episodes.")
                count += c2 - c
        logger.info(f"Podcasts update finished, {count} new episodes total.")

    def handle(self, *args, **options):
        if options["update"]:
            self.update()
        if options["stat"]:
            self.stat()
