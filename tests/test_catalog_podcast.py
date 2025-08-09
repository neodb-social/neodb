import pytest

from catalog.common import *
from catalog.podcast.models import *

# class TestApplePodcast:
#     @pytest.fixture(autouse=True)
#     def setup_data(self):
#         pass

#     def test_parse(self):
#         t_id = "657765158"
#         t_url = "https://podcasts.apple.com/us/podcast/%E5%A4%A7%E5%86%85%E5%AF%86%E8%B0%88/id657765158"
#         t_url2 = "https://podcasts.apple.com/us/podcast/id657765158"
#         p1 = SiteManager.get_site_cls_by_id_type(IdType.ApplePodcast)
#         assert p1 is not None
#         assert p1.validate_url(t_url) == True
#         p2 = SiteManager.get_site_by_url(t_url)
#         assert p1.id_to_url(t_id) == t_url2
#         assert p2.url_to_id(t_url) == t_id

#     @use_local_response
#     def test_scrape(self):
#         t_url = "https://podcasts.apple.com/gb/podcast/the-new-yorker-radio-hour/id1050430296"
#         site = SiteManager.get_site_by_url(t_url)
#         assert site.ready == False
#         assert site.id_value == "1050430296"
#         site.get_resource_ready()
#         assert site.resource.metadata["title"] == "The New Yorker Radio Hour"
#         # assert site.resource.metadata['feed_url'] == 'http://feeds.wnyc.org/newyorkerradiohour'
#         assert site.resource.metadata["feed_url"] == "http://feeds.feedburner.com/newyorkerradiohour"


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

    # @use_local_response
    # def test_scrape_libsyn(self):
    #     t_url = "https://feeds.feedburner.com/TheLesserBonapartes"
    #     site = SiteManager.get_site_by_url(t_url)
    #     site.get_resource_ready()
    #     assert site.ready == True
    #     metadata = site.resource.metadata
    #     assert site.get_item().recent_episodes[0].title is not None
    #     assert site.get_item().recent_episodes[0].link is not None
    #     assert site.get_item().recent_episodes[0].media_url is not None

    @use_local_response
    def test_scrape_anchor(self):
        t_url = "https://anchor.fm/s/64d6bbe0/podcast/rss"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        assert site.ready
        # metadata = site.resource.metadata
        assert site.get_item().cover.url is not None
        assert site.get_item().recent_episodes[0].title is not None
        assert site.get_item().recent_episodes[0].link is not None
        assert site.get_item().recent_episodes[0].media_url is not None

    @use_local_response
    def test_scrape_digforfire(self):
        t_url = "https://www.digforfire.net/digforfire_radio_feed.xml"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        assert site.ready
        # metadata = site.resource.metadata
        assert site.get_item().recent_episodes[0].title is not None
        assert site.get_item().recent_episodes[0].link is not None
        assert site.get_item().recent_episodes[0].media_url is not None

    @use_local_response
    def test_scrape_bbc(self):
        t_url = "https://podcasts.files.bbci.co.uk/b006qykl.rss"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        assert site.ready
        metadata = site.resource.metadata
        assert metadata["title"] == "In Our Time"
        assert metadata["official_site"] == "http://www.bbc.co.uk/programmes/b006qykl"
        assert metadata["genre"] == ["History"]
        assert metadata["host"] == ["BBC Radio 4"]
        assert site.get_item().recent_episodes[0].title is not None
        assert site.get_item().recent_episodes[0].link is not None
        assert site.get_item().recent_episodes[0].media_url is not None

    @use_local_response
    def test_scrape_rsshub(self):
        t_url = "https://rsshub.app/ximalaya/album/51101122/0/shownote"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        assert site.ready
        metadata = site.resource.metadata
        assert metadata["title"] == "梁文道 · 八分"
        assert metadata["official_site"] == "https://www.ximalaya.com/qita/51101122/"
        assert metadata["genre"] == ["人文国学"]
        assert metadata["host"] == ["看理想vistopia"]
        assert site.get_item().recent_episodes[0].title is not None
        assert site.get_item().recent_episodes[0].link is not None
        assert site.get_item().recent_episodes[0].media_url is not None

    @use_local_response
    def test_scrape_typlog(self):
        t_url = "https://tiaodao.typlog.io/feed.xml"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        assert site.ready
        metadata = site.resource.metadata
        assert metadata["title"] == "跳岛FM"
        assert metadata["official_site"] == "https://tiaodao.typlog.io/"
        assert metadata["genre"] == ["Arts", "Books"]
        assert metadata["host"] == ["中信出版·大方"]
        assert site.get_item().recent_episodes[0].title is not None
        assert site.get_item().recent_episodes[0].link is not None
        assert site.get_item().recent_episodes[0].media_url is not None

    # @use_local_response
    # def test_scrape_lizhi(self):
    #     t_url = "http://rss.lizhi.fm/rss/14275.xml"
    #     site = SiteManager.get_site_by_url(t_url)
    #     assert site is not None
    #     site.get_resource_ready()
    #     assert site.ready == True
    #     metadata = site.resource.metadata
    #     assert metadata["title"] == "大内密谈"
    #     assert metadata["genre"] == ["other"]
    #     assert metadata["host"] == ["大内密谈"]
    #     assert site.get_item().recent_episodes[0].title is not None
    #     assert site.get_item().recent_episodes[0].link is not None
    #     assert site.get_item().recent_episodes[0].media_url is not None
