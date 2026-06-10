from datetime import timedelta

from django.core.cache import cache
from loguru import logger

from catalog.models import item_categories
from catalog.views import visible_categories
from common.models import BaseJob, JobManager


@JobManager.register
class CatalogStats(BaseJob):
    """Calculate and cache statistics for the about page."""

    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(minutes=30)

    CACHE_KEY = "catalog_stats"

    def run(self):
        logger.info("StatsJob: Calculating item counts")
        stats = []
        for cat in visible_categories(None) or item_categories().keys():
            count = 0
            for cls in item_categories()[cat]:
                count += cls.objects.filter().count()
            stats.append({"label": cat.label, "value": cat.value, "count": count})
        cache.set(self.CACHE_KEY, stats, 3600 * 24 * 7)
        logger.info(f"StatsJob: Cached stats: {stats}")
