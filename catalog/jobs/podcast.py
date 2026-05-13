import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.db import close_old_connections
from django.db.models import Max
from django.utils import timezone
from loguru import logger

from catalog.models import IdType, Podcast, PodcastEpisode
from catalog.sites import RSS
from common.models import BaseJob, JobManager

FRESH_DELAY = timedelta(hours=2)
MID_DELAY = timedelta(hours=12)
COLD_DELAY = timedelta(days=7)
ACTIVE_THRESHOLD = timedelta(days=30)
RECENT_THRESHOLD = timedelta(days=180)
MAX_FAILURE_EXP = 6  # cap exponential backoff at 2^6 = 64x base delay
MAX_FETCH_WORKERS = 16


def _tier_delay(last_pub, now):
    if last_pub is None:
        return COLD_DELAY
    age = now - last_pub
    if age <= ACTIVE_THRESHOLD:
        return FRESH_DELAY
    if age <= RECENT_THRESHOLD:
        return MID_DELAY
    return COLD_DELAY


def _is_due(podcast: Podcast, last_pub, now) -> bool:
    base = _tier_delay(last_pub, now)
    failures = max(0, int(podcast.feed_consecutive_failures or 0))
    delay = base * (2 ** min(failures, MAX_FAILURE_EXP)) if failures else base
    last_fetched = podcast.feed_last_fetched_at
    if last_fetched is None:
        return True
    return (now - last_fetched) >= delay


def _fetch_one(podcast: Podcast):
    feed, etag, last_modified, status = RSS.fetch_feed_with_metadata(
        podcast.feed_url,
        podcast.feed_etag or "",
        podcast.feed_last_modified or "",
    )
    return podcast, feed, etag, last_modified, status


@JobManager.register
class PodcastUpdater(BaseJob):
    @classmethod
    def get_interval(cls) -> timedelta:
        return timedelta(hours=2)

    def run(self):
        logger.info("Podcasts update start.")
        start = time.time()
        now = timezone.now()

        qs = Podcast.objects.filter(
            is_deleted=False,
            merged_to_item__isnull=True,
            primary_lookup_id_type=IdType.RSS,
            primary_lookup_id_value__isnull=False,
        )

        last_pub_map = dict(
            PodcastEpisode.objects.filter(program__in=qs)
            .values("program_id")
            .annotate(last=Max("pub_date"))
            .values_list("program_id", "last")
        )

        candidates: list[Podcast] = []
        skipped = 0
        for p in qs.iterator(chunk_size=500):
            if _is_due(p, last_pub_map.get(p.pk), now):
                candidates.append(p)
            else:
                skipped += 1
        logger.info(
            f"Podcasts: {len(candidates)} due, {skipped} skipped by tier/backoff."
        )

        new_episodes_total = 0
        updated = 0
        not_modified = 0
        failed = 0

        if candidates:
            with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as ex:
                for p, feed, etag, last_modified, status in ex.map(
                    _fetch_one, candidates
                ):
                    added = 0
                    if status == 200 and feed is not None:
                        try:
                            added = RSS.update_episodes_from_feed(p, feed)
                        except Exception as e:
                            logger.warning(f"episode write failed for {p}: {e}")
                            status = 0
                    fetched_at = timezone.now()
                    if status == 200:
                        p.feed_etag = etag or ""
                        p.feed_last_modified = last_modified or ""
                        p.feed_consecutive_failures = 0
                        p.feed_last_fetched_at = fetched_at
                        updated += 1
                        if added:
                            logger.info(f"updated {p}, {added} new episodes.")
                    elif status == 304:
                        p.feed_etag = etag or p.feed_etag or ""
                        p.feed_last_modified = (
                            last_modified or p.feed_last_modified or ""
                        )
                        p.feed_consecutive_failures = 0
                        p.feed_last_fetched_at = fetched_at
                        not_modified += 1
                    else:
                        p.feed_consecutive_failures = (
                            int(p.feed_consecutive_failures or 0) + 1
                        )
                        p.feed_last_fetched_at = fetched_at
                        failed += 1
                        logger.warning(f"failed to update {p}")
                    try:
                        p.save(update_fields=["metadata"])
                    except Exception as e:
                        logger.warning(f"failed to persist feed metadata for {p}: {e}")
                    new_episodes_total += added
            close_old_connections()

        t = round(time.time() - start, 3)
        logger.info(
            f"Podcasts update finished in {t}s: "
            f"{updated} updated, {not_modified} unchanged, {failed} failed, "
            f"{skipped} skipped, {new_episodes_total} new episodes."
        )
