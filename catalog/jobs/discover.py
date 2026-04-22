import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, F, Q
from django.db.models.query import prefetch_related_objects
from django.utils import timezone
from loguru import logger

from catalog.models import *
from catalog.sites.fedi import FediverseInstance
from common.models import SITE_PREFERRED_LOCALES, BaseJob, JobManager, SiteConfig
from journal.models import (
    Collection,
    Comment,
    Review,
    ShelfMember,
    TagManager,
    q_item_in_category,
)
from takahe.models import Identity
from takahe.utils import Post

MAX_ITEMS_PER_PERIOD = 12
MAX_DAYS_FOR_PERIOD = 96
MIN_DAYS_FOR_PERIOD = 6
DAYS_FOR_TRENDS = 3


@JobManager.register
class DiscoverGenerator(BaseJob):
    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(minutes=SiteConfig.system.discover_update_interval)

    @property
    def min_marks(self) -> int:
        return SiteConfig.system.min_marks_for_discover

    def get_no_discover_identities(self) -> list:
        return list(
            Identity.objects.filter(discoverable=False).values_list("pk", flat=True)
        )

    def get_popular_posts(
        self,
        days: int = 30,
        min_interaction: int = 1,
        local_only: bool = False,
    ):
        since = timezone.now() - timedelta(days=days)
        domains = FediverseInstance.get_peers_for_search() + [settings.SITE_DOMAIN]
        qs = (
            Post.objects.exclude(state__in=["deleted", "deleted_fanned_out"])
            .filter(author__restriction=0)
            .exclude(author__discoverable=False)
            .filter(
                author__domain__in=domains,
                visibility__in=[0, 1, 4],
                published__gte=since,
            )
            .annotate(num_interactions=Count("interactions"))
            .filter(num_interactions__gte=min_interaction)
            .order_by("-num_interactions", "-published")
        )
        if local_only:
            qs = qs.filter(local=True)
        if (
            SiteConfig.system.discover_filter_language
            and SiteConfig.system.preferred_languages
        ):
            q = None
            for lang in SiteConfig.system.preferred_languages:
                if q:
                    q = q | Q(language__istartswith=lang)
                else:
                    q = Q(language__istartswith=lang)
            if q:
                qs = qs.filter(q)
        return qs

    def _top_post_ids(self, qs, limit: int, max_per_author: int = 2) -> list:
        pks = []
        author_count: dict = {}
        for pk, author_id in qs.values_list("pk", "author_id").iterator():
            if author_count.get(author_id, 0) >= max_per_author:
                continue
            pks.append(pk)
            author_count[author_id] = author_count.get(author_id, 0) + 1
            if len(pks) >= limit:
                break
        return pks

    def get_popular_marked_item_ids(self, category, days, exisiting_ids):
        qs = (
            ShelfMember.objects.filter(q_item_in_category(category))
            .filter(created_time__gt=timezone.now() - timedelta(days=days))
            .exclude(item_id__in=exisiting_ids)
        )
        if SiteConfig.system.discover_show_local_only:
            qs = qs.filter(local=True)
        if SiteConfig.system.discover_filter_language:
            q = None
            for loc in SITE_PREFERRED_LOCALES:
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
            .filter(num__gte=self.min_marks)
            .order_by("-num")[:MAX_ITEMS_PER_PERIOD]
        ]
        return item_ids

    def get_popular_commented_podcast_ids(self, days, exisiting_ids):
        qs = Comment.objects.filter(q_item_in_category(ItemCategory.Podcast)).filter(
            created_time__gt=timezone.now() - timedelta(days=days)
        )
        if SiteConfig.system.discover_show_local_only:
            qs = qs.filter(local=True)
        return list(
            qs.annotate(p=F("item__podcastepisode__program"))
            .filter(p__isnull=False)
            .exclude(p__in=exisiting_ids)
            .values("p")
            .annotate(num=Count("p"))
            .filter(num__gte=self.min_marks)
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
        local = SiteConfig.system.discover_show_local_only
        gallery_categories = [
            ItemCategory.Book,
            ItemCategory.Movie,
            ItemCategory.TV,
            ItemCategory.Game,
            ItemCategory.Music,
            ItemCategory.Podcast,
            ItemCategory.Performance,
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
            items = [i for i in items if not i.is_deleted and not i.merged_to_item_id]
            if category == ItemCategory.TV:
                items = self.cleanup_shows(items)
            key = "trending_" + category.value
            gallery_list.append(
                {
                    "name": key,
                    "category": category,
                }
            )
            for i in items:
                i.tags
                i.rating
                i.rating_count
                i.rating_distribution
            prefetch_related_objects(items, "external_resources")
            editions = [i for i in items if isinstance(i, Edition)]
            if editions:
                prefetch_related_objects(editions, "works")
            cache.set(key, items, timeout=None)

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
            .filter(num__gte=self.min_marks)
            .order_by("-edited_time")
        )
        if local:
            collections = collections.filter(local=True)
        collection_ids = collections.values_list("pk", flat=True)[:40]

        tags = TagManager.popular_tags(days=14, local_only=local)[:40]
        excluding_identities = self.get_no_discover_identities()

        if SiteConfig.system.discover_show_popular_posts:
            reviews = (
                Review.objects.filter(visibility=0)
                .exclude(owner_id__in=excluding_identities)
                .order_by("-created_time")
            )
            if local:
                reviews = reviews.filter(local=True)
            post_ids = (
                set(
                    self._top_post_ids(
                        self.get_popular_posts(28, self.min_marks, local), 5
                    )
                )
                | set(
                    self._top_post_ids(
                        self.get_popular_posts(14, self.min_marks, local), 5
                    )
                )
                | set(
                    self._top_post_ids(
                        self.get_popular_posts(7, self.min_marks, local), 10
                    )
                )
                | set(self._top_post_ids(self.get_popular_posts(1, 0, local), 3))
                | set(reviews.values_list("posts", flat=True)[:5])
            )
        else:
            post_ids = []
        cache.set("public_gallery", gallery_list, timeout=None)
        cache.set("trends_links", trends, timeout=None)
        cache.set("featured_collections", collection_ids, timeout=None)
        cache.set("popular_tags", list(tags), timeout=None)
        cache.set("popular_posts", list(post_ids), timeout=None)
        cache.set("trends_statuses", list(post_ids), timeout=None)
        cache.set("trends_updated", timezone.now(), timeout=None)
        logger.info(
            f"Discover data updated, excluded: {len(excluding_identities)}, trends: {len(trends)}, collections: {len(collection_ids)}, tags: {len(tags)}, posts: {len(post_ids)}."
        )
