from datetime import timedelta

from loguru import logger

from catalog.models import IdType, Podcast
from catalog.sites import RSS
from common.models import BaseJob, JobManager


@JobManager.register
class PodcastUpdater(BaseJob):
    interval = timedelta(hours=2)

    def run(self):
        logger.info("Podcasts update start.")
        count = 0
        qs = Podcast.objects.filter(
            is_deleted=False, merged_to_item__isnull=True
        ).order_by("pk")
        for p in qs:
            if (
                p.primary_lookup_id_type == IdType.RSS
                and p.primary_lookup_id_value is not None
            ):
                logger.info(f"updating {p}")
                c = p.episodes.count()
                site = RSS(p.feed_url)
                r = site.scrape_additional_data()
                if r:
                    c2 = p.episodes.count()
                    logger.info(f"updated {p}, {c2 - c} new episodes.")
                    count += c2 - c
                else:
                    logger.warning(f"failed to update {p}")
        logger.info(f"Podcasts update finished, {count} new episodes total.")
