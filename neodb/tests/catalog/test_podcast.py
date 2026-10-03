from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from catalog.common import *
from catalog.models import *
from catalog.jobs.podcast import (
    COLD_DELAY,
    FRESH_DELAY,
    MID_DELAY,
    PodcastUpdater,
    _fetch_one,
    _is_due,
    _tier_delay,
)
from catalog.sites.rss import RSS, _episode_duration


@pytest.mark.parametrize(
    "total_time,expected",
    [(2032, 2032), (0, None), (-5, None), (2**31, None), (None, None), ("60", None)],
)
def test_episode_duration(total_time, expected):
    assert _episode_duration({"total_time": total_time}) == expected


@pytest.mark.django_db(databases="__all__")
class TestPodcastRSSFeed:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        pass

    def test_parse(self):
        t_id = "podcasts.files.bbci.co.uk/b006qykl.rss"
        t_url = "https://podcasts.files.bbci.co.uk/b006qykl.rss"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.ID_TYPE == IdType.RSS
        assert site.id_value == t_id

    @use_local_response
    def test_scrape_anchor(self):
        t_url = "https://anchor.fm/s/64d6bbe0/podcast/rss"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.ready
        # metadata = site.resource.metadata
        item = site.get_item()
        assert item is not None
        assert isinstance(item, Podcast)
        assert item.cover is not None
        assert item.cover.url is not None
        assert item.recent_episodes is not None
        assert len(item.recent_episodes) > 0
        assert item.recent_episodes[0].title is not None
        assert item.recent_episodes[0].link is not None
        assert item.recent_episodes[0].media_url is not None
        episode = PodcastEpisode.objects.get(
            program=item, guid="0951c1ff-ad98-42b4-a21e-54cf36cedd0c"
        )
        assert episode.duration == 2032

    @use_local_response
    def test_scrape_digforfire(self):
        t_url = "https://www.digforfire.net/digforfire_radio_feed.xml"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.ready
        # metadata = site.resource.metadata
        item = site.get_item()
        assert item is not None
        assert isinstance(item, Podcast)
        assert item.recent_episodes is not None
        assert len(item.recent_episodes) > 0
        assert item.recent_episodes[0].title is not None
        assert item.recent_episodes[0].link is not None
        assert item.recent_episodes[0].media_url is not None
        # podcastparser reports total_time 0 for every episode of this feed
        assert not PodcastEpisode.objects.filter(
            program=item, duration__isnull=False
        ).exists()

    @use_local_response
    def test_scrape_bbc(self):
        t_url = "https://podcasts.files.bbci.co.uk/b006qykl.rss"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        metadata = site.resource.metadata
        assert metadata["title"] == "In Our Time"
        assert metadata["official_site"] == "http://www.bbc.co.uk/programmes/b006qykl"
        assert metadata["genre"] == ["History"]
        assert metadata["host"] == ["BBC Radio 4"]
        item = site.get_item()
        assert item is not None
        assert isinstance(item, Podcast)
        assert item.recent_episodes is not None
        assert len(item.recent_episodes) > 0
        assert item.recent_episodes[0].title is not None
        assert item.recent_episodes[0].link is not None
        assert item.recent_episodes[0].media_url is not None

    @use_local_response
    def test_scrape_rsshub(self):
        t_url = "https://rsshub.app/ximalaya/album/51101122/0/shownote"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        metadata = site.resource.metadata
        assert metadata["title"] == "梁文道 · 八分"
        assert metadata["official_site"] == "https://www.ximalaya.com/qita/51101122/"
        assert metadata["genre"] == ["人文国学"]
        assert metadata["host"] == ["看理想vistopia"]
        item = site.get_item()
        assert item is not None
        assert isinstance(item, Podcast)
        assert item.recent_episodes is not None
        assert len(item.recent_episodes) > 0
        assert item.recent_episodes[0].title is not None
        assert item.recent_episodes[0].link is not None
        assert item.recent_episodes[0].media_url is not None

    @use_local_response
    def test_scrape_typlog(self):
        t_url = "https://tiaodao.typlog.io/feed.xml"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.ready
        assert site.resource is not None
        metadata = site.resource.metadata
        assert metadata["title"] == "跳岛FM"
        assert metadata["official_site"] == "https://tiaodao.typlog.io/"
        assert metadata["genre"] == ["Arts", "Books"]
        assert metadata["host"] == ["中信出版·大方"]
        item = site.get_item()
        assert item is not None
        assert isinstance(item, Podcast)
        assert item.recent_episodes is not None
        assert len(item.recent_episodes) > 0
        assert item.recent_episodes[0].title is not None
        assert item.recent_episodes[0].link is not None
        assert item.recent_episodes[0].media_url is not None

    @use_local_response
    def test_scrape_idempotent_and_batch_optimized(self):
        """Scraping same feed twice should skip existing episodes."""
        t_url = "https://podcasts.files.bbci.co.uk/b006qykl.rss"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.ready
        item = site.get_item()
        assert item is not None
        episode_count = PodcastEpisode.objects.filter(program=item).count()
        assert episode_count > 0
        # Scrape again - should skip existing episodes
        with CaptureQueriesContext(connection) as ctx:
            site.scrape_additional_data()
        # Should NOT have per-episode SELECT queries for existing episodes
        # The batch pre-fetch means we only need 1 SELECT for all existing guids
        episode_select_queries = [
            q
            for q in ctx.captured_queries
            if "catalog_podcastepisode" in q["sql"]
            and "guid" in q["sql"]
            and "program_id" in q["sql"]
            and "IN" not in q["sql"].upper()
        ]
        assert len(episode_select_queries) == 0
        # Episode count should be unchanged
        assert PodcastEpisode.objects.filter(program=item).count() == episode_count


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (None, COLD_DELAY),
        (timedelta(days=-1), FRESH_DELAY),
        (timedelta(days=30), FRESH_DELAY),
        (timedelta(days=30, seconds=1), MID_DELAY),
        (timedelta(days=180), MID_DELAY),
        (timedelta(days=180, seconds=1), COLD_DELAY),
    ],
)
def test_podcast_polling_tiers(age: timedelta | None, expected: timedelta) -> None:
    now = timezone.now()
    published = now - age if age is not None else None
    assert _tier_delay(published, now) == expected


@pytest.mark.parametrize(
    ("failures", "delay"), [(-1, 2), (0, 2), (1, 4), (6, 128), (10, 128)]
)
def test_podcast_backoff_boundary(failures: int, delay: int) -> None:
    now = timezone.now()
    podcast = Podcast(feed_consecutive_failures=failures)
    published = now - timedelta(days=1)
    assert _is_due(podcast, published, now)
    podcast.feed_last_fetched_at = now - timedelta(hours=delay) + timedelta(seconds=1)
    assert not _is_due(podcast, published, now)
    podcast.feed_last_fetched_at = now - timedelta(hours=delay)
    assert _is_due(podcast, published, now)


def test_podcast_fetch_closes_thread_connections_on_failure() -> None:
    podcast = Podcast(
        primary_lookup_id_type=IdType.RSS, primary_lookup_id_value="feed.example/rss"
    )
    with (
        patch.object(
            RSS, "fetch_feed_with_metadata", side_effect=ValueError("bad feed")
        ),
        patch("catalog.jobs.podcast.connections.close_all") as close,
    ):
        with pytest.raises(ValueError, match="bad feed"):
            _fetch_one(podcast)
    close.assert_called_once()


@pytest.mark.django_db(databases="__all__", transaction=True)
class TestPodcastUpdater:
    def _podcast(self, name: str = "update", **metadata: object) -> Podcast:
        return Podcast.objects.create(
            title=name,
            primary_lookup_id_type=IdType.RSS,
            primary_lookup_id_value=f"feed.example/{name}.xml",
            **metadata,
        )

    def _feed(self, now: datetime) -> dict[str, object]:
        return {
            "episodes": [
                {
                    "guid": "new-episode",
                    "title": "New episode",
                    "published": now.timestamp(),
                    "enclosures": [{"url": "https://feed.example/episode.mp3"}],
                }
            ]
        }

    def test_empty_catalog_never_fetches(self) -> None:
        with patch.object(RSS, "fetch_feed_with_metadata") as fetch:
            PodcastUpdater().run()
        fetch.assert_not_called()
        assert PodcastUpdater.get_interval() == timedelta(hours=2)

    def test_success_inserts_episodes_and_resets_failures(self) -> None:
        now = timezone.now()
        podcast = self._podcast(
            feed_etag="old-etag",
            feed_last_modified="old-modified",
            feed_consecutive_failures=3,
        )
        with patch.object(
            RSS,
            "fetch_feed_with_metadata",
            return_value=(self._feed(now), "new-etag", "new-modified", 200),
        ) as fetch:
            PodcastUpdater().run()
        fetch.assert_called_once_with(podcast.feed_url, "old-etag", "old-modified")
        podcast.refresh_from_db()
        assert podcast.feed_etag == "new-etag"
        assert podcast.feed_last_modified == "new-modified"
        assert podcast.feed_consecutive_failures == 0
        assert podcast.feed_last_fetched_at is not None
        episode = podcast.episodes.get()
        assert episode.guid == "new-episode"
        assert episode.title == "New episode"
        assert episode.media_url == "https://feed.example/episode.mp3"
        with patch.object(RSS, "fetch_feed_with_metadata") as repeat_fetch:
            PodcastUpdater().run()
        repeat_fetch.assert_not_called()
        assert podcast.episodes.count() == 1

    @pytest.mark.parametrize(
        ("etag", "modified"), [("", ""), ("new-etag", "new-modified")]
    )
    def test_not_modified_keeps_episodes_and_resets_failures(
        self, etag: str, modified: str
    ) -> None:
        podcast = self._podcast(
            feed_etag="old-etag",
            feed_last_modified="old-modified",
            feed_consecutive_failures=2,
        )
        episode = PodcastEpisode.objects.create(
            program=podcast,
            guid="existing-episode",
            title="Existing episode",
            pub_date=timezone.now(),
        )
        with (
            patch.object(
                RSS,
                "fetch_feed_with_metadata",
                return_value=(None, etag, modified, 304),
            ),
            patch.object(RSS, "update_episodes_from_feed") as update,
        ):
            PodcastUpdater().run()
        update.assert_not_called()
        podcast.refresh_from_db()
        assert podcast.feed_etag == (etag or "old-etag")
        assert podcast.feed_last_modified == (modified or "old-modified")
        assert podcast.feed_consecutive_failures == 0
        assert podcast.feed_last_fetched_at is not None
        kept = podcast.episodes.get()
        assert kept.pk == episode.pk
        assert kept.title == "Existing episode"

    @pytest.mark.parametrize("status", [0, 404, 503])
    def test_fetch_failure_preserves_validators_and_increments_backoff(
        self, status: int
    ) -> None:
        podcast = self._podcast(
            feed_etag="old-etag",
            feed_last_modified="old-modified",
            feed_consecutive_failures=2,
        )
        with patch.object(
            RSS, "fetch_feed_with_metadata", return_value=(None, "", "", status)
        ):
            PodcastUpdater().run()
        podcast.refresh_from_db()
        assert podcast.feed_etag == "old-etag"
        assert podcast.feed_last_modified == "old-modified"
        assert podcast.feed_consecutive_failures == 3
        assert podcast.feed_last_fetched_at is not None
        assert not podcast.episodes.exists()

    def test_episode_write_failure_is_retriable(self) -> None:
        podcast = self._podcast(feed_etag="old-etag")
        with (
            patch.object(
                RSS,
                "fetch_feed_with_metadata",
                return_value=(self._feed(timezone.now()), "new-etag", "", 200),
            ),
            patch.object(
                RSS, "update_episodes_from_feed", side_effect=ValueError("bad episode")
            ),
        ):
            PodcastUpdater().run()
        podcast.refresh_from_db()
        assert podcast.feed_etag == "old-etag"
        assert podcast.feed_consecutive_failures == 1
        assert podcast.feed_last_fetched_at is not None

    def test_only_due_rss_originals_are_fetched(self) -> None:
        due = self._podcast("due")
        recent = self._podcast("recent", feed_last_fetched_at=timezone.now())
        PodcastEpisode.objects.create(
            program=recent, guid="recent", title="Recent", pub_date=timezone.now()
        )
        deleted = self._podcast("deleted")
        merged = self._podcast("merged")
        non_rss = self._podcast("non-rss")
        no_feed = self._podcast("no-feed")
        Podcast.objects.filter(pk=deleted.pk).update(is_deleted=True)
        Podcast.objects.filter(pk=merged.pk).update(merged_to_item=due)
        Podcast.objects.filter(pk=non_rss.pk).update(
            primary_lookup_id_type=IdType.ApplePodcast
        )
        Podcast.objects.filter(pk=no_feed.pk).update(primary_lookup_id_value=None)
        with patch.object(
            RSS, "fetch_feed_with_metadata", return_value=(None, "", "", 304)
        ) as fetch:
            PodcastUpdater().run()
        fetch.assert_called_once_with(due.feed_url, "", "")
        for podcast in (deleted, merged, non_rss, no_feed):
            podcast.refresh_from_db()
            assert podcast.feed_last_fetched_at is None
