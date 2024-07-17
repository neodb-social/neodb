import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, F, Q
from django.utils import timezone
from loguru import logger

from boofilsic.settings import MIN_MARKS_FOR_DISCOVER
from catalog.models import *
from common.models import BaseJob, JobManager
from common.models.lang import PREFERRED_LOCALES
from journal.models import (
    Collection,
    Comment,
    Review,
    ShelfMember,
    TagManager,
    q_item_in_category,
)
from takahe.utils import Takahe
from users.models import APIdentity

MAX_ITEMS_PER_PERIOD = 12
MIN_MARKS = settings.MIN_MARKS_FOR_DISCOVER
MAX_DAYS_FOR_PERIOD = 96
MIN_DAYS_FOR_PERIOD = 6
DAYS_FOR_TRENDS = 3


@JobManager.register
class DiscoverGenerator(BaseJob):
    interval = timedelta(minutes=settings.DISCOVER_UPDATE_INTERVAL)

    def get_popular_marked_item_ids(self, category, days, exisiting_ids):
        qs = (
            ShelfMember.objects.filter(q_item_in_category(category))
            .filter(created_time__gt=timezone.now() - timedelta(days=days))
            .exclude(item_id__in=exisiting_ids)
        )
        if settings.DISCOVER_SHOW_LOCAL_ONLY:
            qs = qs.filter(local=True)
        if settings.DISCOVER_FILTER_LANGUAGE:
            q = None
            for loc in PREFERRED_LOCALES:
                if q:
                    q = q | Q(item__metadata__localized_title__contains=[{"lang": loc}])
                else:
                    q = Q(item__metadata__localized_title__contains=[{"lang": loc}])
            if q:
                qs = qs.filter(q)
        item_ids = [
            m["item_id"]
            for m in qs.values("item_id")
            .annotate(num=Count("item_id"))
            .filter(num__gte=MIN_MARKS)
            .order_by("-num")[:MAX_ITEMS_PER_PERIOD]
        ]
        return item_ids

    def get_popular_commented_podcast_ids(self, days, exisiting_ids):
        qs = Comment.objects.filter(q_item_in_category(ItemCategory.Podcast)).filter(
            created_time__gt=timezone.now() - timedelta(days=days)
        )
        if settings.DISCOVER_SHOW_LOCAL_ONLY:
            qs = qs.filter(local=True)
        return list(
            qs.annotate(p=F("item__podcastepisode__program"))
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
            if season.show:
                items.remove(season)
                if season.show not in items:
                    items.append(season.show)
        return items

    def run(self):
        logger.info("Discover data update start.")
        local = settings.DISCOVER_SHOW_LOCAL_ONLY
        gallery_categories = [
            ItemCategory.Book,
            ItemCategory.Movie,
            ItemCategory.TV,
            ItemCategory.Game,
            ItemCategory.Music,
            ItemCategory.Podcast,
        ]
        gallery_list = []
        trends = []
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
                    "category": category,
                    "items": items,
                }
            )
            item_ids = self.get_popular_marked_item_ids(category, DAYS_FOR_TRENDS, [])[
                :5
            ]
            if category == ItemCategory.Podcast:
                item_ids += self.get_popular_commented_podcast_ids(
                    DAYS_FOR_TRENDS, item_ids
                )[:3]
            for i in Item.objects.filter(pk__in=set(item_ids)):
                cnt = ShelfMember.objects.filter(
                    item=i, created_time__gt=timezone.now() - timedelta(days=7)
                ).count()
                trends.append(
                    {
                        "title": i.display_title,
                        "description": i.display_description,
                        "url": i.absolute_url,
                        "image": i.cover_image_url or "",
                        "provider_name": str(i.category.label),
                        "history": [
                            {
                                "day": str(int(time.time() / 86400 - 3) * 86400),
                                "accounts": str(cnt),
                                "uses": str(cnt),
                            }
                        ],
                    }
                )
        trends.sort(key=lambda x: int(x["history"][0]["accounts"]), reverse=True)

        collections = (
            Collection.objects.filter(visibility=0)
            .annotate(num=Count("interactions"))
            .filter(num__gte=MIN_MARKS)
            .order_by("-edited_time")
        )
        if local:
            collections = collections.filter(local=True)
        collection_ids = collections.values_list("pk", flat=True)[:40]

        tags = TagManager.popular_tags(days=14, local_only=local)[:40]
        excluding_identities = Takahe.get_no_discover_identities()

        if settings.NEODB_DISCOVER_SHOW_POPULAR_POSTS:
            reviews = (
                Review.objects.filter(visibility=0)
                .exclude(owner_id__in=excluding_identities)
                .order_by("-created_time")
            )
            if local:
                reviews = reviews.filter(local=True)
            post_ids = (
                set(
                    Takahe.get_popular_posts(
                        28, settings.MIN_MARKS_FOR_DISCOVER, excluding_identities, local
                    ).values_list("pk", flat=True)[:5]
                )
                | set(
                    Takahe.get_popular_posts(
                        14, settings.MIN_MARKS_FOR_DISCOVER, excluding_identities, local
                    ).values_list("pk", flat=True)[:5]
                )
                | set(
                    Takahe.get_popular_posts(
                        7, settings.MIN_MARKS_FOR_DISCOVER, excluding_identities, local
                    ).values_list("pk", flat=True)[:10]
                )
                | set(
                    Takahe.get_popular_posts(
                        1, 0, excluding_identities, local
                    ).values_list("pk", flat=True)[:3]
                )
                | set(reviews.values_list("posts", flat=True)[:5])
            )
        else:
            post_ids = []
        cache.set("public_gallery", gallery_list, timeout=None)
        cache.set("trends_links", trends, timeout=None)
        cache.set("featured_collections", collection_ids, timeout=None)
        cache.set("popular_tags", list(tags), timeout=None)
        cache.set("popular_posts", list(post_ids), timeout=None)
        logger.info(
            f"Discover data updated, excluded: {len(excluding_identities)}, trends: {len(trends)}, collections: {len(collection_ids)}, tags: {len(tags)}, posts: {len(post_ids)}."
        )
