import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalog.common import *
from catalog.models import *


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
