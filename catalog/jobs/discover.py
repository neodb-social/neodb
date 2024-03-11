from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from loguru import logger

from catalog.models import *
from common.models import BaseJob, JobManager
from journal.models import Comment, ShelfMember, q_item_in_category

MAX_ITEMS_PER_PERIOD = 12
MIN_MARKS = settings.MIN_MARKS_FOR_DISCOVER
MAX_DAYS_FOR_PERIOD = 96
MIN_DAYS_FOR_PERIOD = 6


@JobManager.register
class DiscoverGenerator(BaseJob):
    interval = timedelta(hours=1)

    def get_popular_marked_item_ids(self, category, days, exisiting_ids):
        item_ids = [
            m["item_id"]
            for m in ShelfMember.objects.filter(q_item_in_category(category))
            .filter(created_time__gt=timezone.now() - timedelta(days=days))
            .exclude(item_id__in=exisiting_ids)
            .values("item_id")
            .annotate(num=Count("item_id"))
            .filter(num__gte=MIN_MARKS)
            .order_by("-num")[:MAX_ITEMS_PER_PERIOD]
        ]
        return item_ids

    def get_popular_commented_podcast_ids(self, days, exisiting_ids):
        return list(
            Comment.objects.filter(q_item_in_category(ItemCategory.Podcast))
            .filter(created_time__gt=timezone.now() - timedelta(days=days))
            .annotate(p=F("item__podcastepisode__program"))
            .filter(p__isnull=False)
            .exclude(p__in=exisiting_ids)
            .values("p")
            .annotate(num=Count("p"))
            .filter(num__gte=MIN_MARKS)
            .order_by("-num")
            .values_list("p", flat=True)[:MAX_ITEMS_PER_PERIOD]
        )

    def cleanup_shows(self, items):
        seasons = [i for i in items if i.__class__ == TVSeason]
        for season in seasons:
            if season.show in items:
                items.remove(season.show)
        return items

    def run(self):
        logger.info("Discover data update start.")
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
            days = MAX_DAYS_FOR_PERIOD
            item_ids = []
            while days >= MIN_DAYS_FOR_PERIOD:
                ids = self.get_popular_marked_item_ids(category, days, item_ids)
                logger.info(f"Most marked {category} in last {days} days: {len(ids)}")
                item_ids = ids + item_ids
                days //= 2
            if category == ItemCategory.Podcast:
                days = MAX_DAYS_FOR_PERIOD // 4
                extra_ids = self.get_popular_commented_podcast_ids(days, item_ids)
                logger.info(
                    f"Most commented podcast in last {days} days: {len(extra_ids)}"
                )
                item_ids = extra_ids + item_ids
            items = [Item.objects.get(pk=i) for i in item_ids]
            if category == ItemCategory.TV:
                items = self.cleanup_shows(items)
            gallery_list.append(
                {
                    "name": "popular_" + category.value,
                    "title": ""
                    + (category.label if category != ItemCategory.Book else _("Book")),
                    "items": items,
                }
            )
        cache.set(cache_key, gallery_list, timeout=None)
        logger.info("Discover data updated.")
