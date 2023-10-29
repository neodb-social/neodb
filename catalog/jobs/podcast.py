import pprint
from datetime import timedelta
from time import sleep

from loguru import logger

from catalog.common.models import IdType
from catalog.models import Podcast
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
                site.scrape_additional_data()
                c2 = p.episodes.count()
                logger.info(f"updated {p}, {c2-c} new episodes.")
                count += c2 - c
        logger.info(f"Podcasts update finished, {count} new episodes total.")
