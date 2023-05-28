from django.core.management.base import BaseCommand
from django.core.cache import cache
import pprint
from catalog.models import *
from journal.models import ShelfMember, query_item_category, ItemCategory
from datetime import timedelta
from django.utils import timezone
from django.db.models import Count


MAX_GALLERY_ITEMS = 64
MIN_MARKS = 3


class Command(BaseCommand):
    help = "catalog app utilities"

    def add_arguments(self, parser):
        parser.add_argument(
            "--update",
            action="store_true",
            help="generate discover data",
        )

    def get_popular_item_ids(self, category, days):
        # self.stdout.write(f"Generating popular {category} items for {days} days...")
        item_ids = [
            m["item_id"]
            for m in ShelfMember.objects.filter(query_item_category(category))
            .filter(created_time__gt=timezone.now() - timedelta(days=days))
            .values("item_id")
            .annotate(num=Count("item_id"))
            .filter(num__gte=MIN_MARKS)
            .order_by("-num")[:MAX_GALLERY_ITEMS]
        ]
        return item_ids

    def cleanup_shows(self, items):
        seasons = [i for i in items if i.__class__ == TVSeason]
        for season in seasons:
            if season.show in items:
                items.remove(season.show)
        return items

    def handle(self, *args, **options):
        if options["update"]:
            cache_key = "public_gallery"
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
                days = 128
                item_ids = []
                while days > 4:
                    ids = [
                        i
                        for i in self.get_popular_item_ids(category, days)
                        if i not in item_ids
                    ]
                    if len(ids) > MAX_GALLERY_ITEMS // 5:
                        ids = ids[: MAX_GALLERY_ITEMS // 5]
                    self.stdout.write(f"{category} for last {days} days: {len(ids)}")
                    item_ids = ids + item_ids
                    days //= 2
                items = list(Item.objects.filter(id__in=item_ids))
                if category == ItemCategory.TV:
                    items = self.cleanup_shows(items)
                gallery_list.append(
                    {
                        "name": "popular_" + category.value,
                        "title": ""
                        + (category.label if category != ItemCategory.Book else "图书"),
                        "items": items,
                    }
                )
            cache.set(cache_key, gallery_list, timeout=None)
        self.stdout.write(self.style.SUCCESS(f"Done."))
