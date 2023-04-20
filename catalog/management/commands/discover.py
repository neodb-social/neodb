from django.core.management.base import BaseCommand
from django.core.cache import cache
import pprint
from catalog.models import *
from journal.models import ShelfMember, query_item_category, ItemCategory
from datetime import timedelta
from django.utils import timezone
from django.db.models import Count


class Command(BaseCommand):
    help = "catalog app utilities"

    def add_arguments(self, parser):
        parser.add_argument(
            "--update",
            action="store_true",
            help="generate discover data",
        )

    def handle(self, *args, **options):
        if options["update"]:
            cache_key = "public_gallery_list"
            gallery_categories = [
                ItemCategory.Book,
                ItemCategory.Movie,
                ItemCategory.TV,
                ItemCategory.Game,
                ItemCategory.Music,
                ItemCategory.Podcast,
            ]
            gallery_list = []
            for category in gallery_categories:
                item_ids = [
                    m.item_id
                    for m in ShelfMember.objects.filter(query_item_category(category))
                    .filter(created_time__gt=timezone.now() - timedelta(days=42))
                    .annotate(num=Count("item_id"))
                    .order_by("-num")[:100]
                ]
                gallery_list.append(
                    {
                        "name": "popular_" + category.value,
                        "title": "热门"
                        + (category.label if category != ItemCategory.Book else "图书"),
                        "item_ids": item_ids,
                    }
                )
            cache.set(cache_key, gallery_list, timeout=None)

        self.stdout.write(self.style.SUCCESS(f"Done."))
