from django.core.management.base import BaseCommand
from django.core.cache import cache
import pprint
from catalog.models import *
from journal.models import ShelfMember, query_item_category, ItemCategory
from datetime import timedelta
from django.utils import timezone
from django.db.models import Count


MAX_GALLERY_ITEMS = 42


class Command(BaseCommand):
    help = "catalog app utilities"

    def add_arguments(self, parser):
        parser.add_argument(
            "--update",
            action="store_true",
            help="generate discover data",
        )

    def get_popular_item_ids(self, category, days):
        self.stdout.write(f"Generating popular {category} items for {days} days...")
        item_ids = [
            m["item_id"]
            for m in ShelfMember.objects.filter(query_item_category(category))
            .filter(created_time__gt=timezone.now() - timedelta(days=days))
            .values("item_id")
            .annotate(num=Count("item_id"))
            .order_by("-num")[:MAX_GALLERY_ITEMS]
        ]
        return item_ids

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
                days = 30
                item_ids = []
                while len(item_ids) < MAX_GALLERY_ITEMS / 2 and days < 150:
                    item_ids = self.get_popular_item_ids(category, days)
                    days *= 3
                items = list(
                    Item.objects.filter(id__in=item_ids).order_by("-created_time")
                )
                if category == ItemCategory.TV:
                    seasons = [i for i in items if i.__class__ == TVSeason]
                    for season in seasons:
                        if season.show in items:
                            items.remove(season.show)
                gallery_list.append(
                    {
                        "name": "popular_" + category.value,
                        "title": "热门"
                        + (category.label if category != ItemCategory.Book else "图书"),
                        "items": items,
                    }
                )
            cache.set(cache_key, gallery_list, timeout=None)
        self.stdout.write(self.style.SUCCESS(f"Done."))
