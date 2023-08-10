from django.test import TestCase

from catalog.common import *
from catalog.podcast.models import *

# class ApplePodcastTestCase(TestCase):
#     def setUp(self):
#         pass

#     def test_parse(self):
#         t_id = "657765158"
#         t_url = "https://podcasts.apple.com/us/podcast/%E5%A4%A7%E5%86%85%E5%AF%86%E8%B0%88/id657765158"
#         t_url2 = "https://podcasts.apple.com/us/podcast/id657765158"
#         p1 = SiteManager.get_site_cls_by_id_type(IdType.ApplePodcast)
#         self.assertIsNotNone(p1)
#         self.assertEqual(p1.validate_url(t_url), True)
#         p2 = SiteManager.get_site_by_url(t_url)
#         self.assertEqual(p1.id_to_url(t_id), t_url2)
#         self.assertEqual(p2.url_to_id(t_url), t_id)

#     @use_local_response
#     def test_scrape(self):
#         t_url = "https://podcasts.apple.com/gb/podcast/the-new-yorker-radio-hour/id1050430296"
#         site = SiteManager.get_site_by_url(t_url)
#         self.assertEqual(site.ready, False)
#         self.assertEqual(site.id_value, "1050430296")
#         site.get_resource_ready()
#         self.assertEqual(site.resource.metadata["title"], "The New Yorker Radio Hour")
#         # self.assertEqual(site.resource.metadata['feed_url'], 'http://feeds.wnyc.org/newyorkerradiohour')
#         self.assertEqual(
#             site.resource.metadata["feed_url"],
#             "http://feeds.feedburner.com/newyorkerradiohour",
#         )


class PodcastRSSFeedTestCase(TestCase):
    def setUp(self):
        pass

    def test_parse(self):
        t_id = "podcasts.files.bbci.co.uk/b006qykl.rss"
        t_url = "https://podcasts.files.bbci.co.uk/b006qykl.rss"
        site = SiteManager.get_site_by_url(t_url)
        self.assertIsNotNone(site)
        self.assertEqual(site.ID_TYPE, IdType.RSS)
        self.assertEqual(site.id_value, t_id)

    # @use_local_response
    # def test_scrape_libsyn(self):
    #     t_url = "https://feeds.feedburner.com/TheLesserBonapartes"
    #     site = SiteManager.get_site_by_url(t_url)
    #     site.get_resource_ready()
    #     self.assertEqual(site.ready, True)
    #     metadata = site.resource.metadata
    #     self.assertIsNotNone(site.get_item().recent_episodes[0].title)
    #     self.assertIsNotNone(site.get_item().recent_episodes[0].link)
    #     self.assertIsNotNone(site.get_item().recent_episodes[0].media_url)

    @use_local_response
    def test_scrape_anchor(self):
        t_url = "https://anchor.fm/s/64d6bbe0/podcast/rss"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        metadata = site.resource.metadata
        self.assertIsNotNone(site.get_item().cover.url)
        self.assertIsNotNone(site.get_item().recent_episodes[0].title)
        self.assertIsNotNone(site.get_item().recent_episodes[0].link)
        self.assertIsNotNone(site.get_item().recent_episodes[0].media_url)

    @use_local_response
    def test_scrape_digforfire(self):
        t_url = "https://www.digforfire.net/digforfire_radio_feed.xml"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        metadata = site.resource.metadata
        self.assertIsNotNone(site.get_item().recent_episodes[0].title)
        self.assertIsNotNone(site.get_item().recent_episodes[0].link)
        self.assertIsNotNone(site.get_item().recent_episodes[0].media_url)

    @use_local_response
    def test_scrape_bbc(self):
        t_url = "https://podcasts.files.bbci.co.uk/b006qykl.rss"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        metadata = site.resource.metadata
        self.assertEqual(metadata["title"], "In Our Time")
        self.assertEqual(
            metadata["official_site"], "http://www.bbc.co.uk/programmes/b006qykl"
        )
        self.assertEqual(metadata["genre"], ["History"])
        self.assertEqual(metadata["hosts"], ["BBC Radio 4"])
        self.assertIsNotNone(site.get_item().recent_episodes[0].title)
        self.assertIsNotNone(site.get_item().recent_episodes[0].link)
        self.assertIsNotNone(site.get_item().recent_episodes[0].media_url)

    @use_local_response
    def test_scrape_rsshub(self):
        t_url = "https://rsshub.app/ximalaya/album/51101122/0/shownote"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        metadata = site.resource.metadata
        self.assertEqual(metadata["title"], "梁文道 · 八分")
        self.assertEqual(
            metadata["official_site"], "https://www.ximalaya.com/qita/51101122/"
        )
        self.assertEqual(metadata["genre"], ["人文国学"])
        self.assertEqual(metadata["hosts"], ["看理想vistopia"])
        self.assertIsNotNone(site.get_item().recent_episodes[0].title)
        self.assertIsNotNone(site.get_item().recent_episodes[0].link)
        self.assertIsNotNone(site.get_item().recent_episodes[0].media_url)

    @use_local_response
    def test_scrape_typlog(self):
        t_url = "https://tiaodao.typlog.io/feed.xml"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        metadata = site.resource.metadata
        self.assertEqual(metadata["title"], "跳岛FM")
        self.assertEqual(metadata["official_site"], "https://tiaodao.typlog.io/")
        self.assertEqual(metadata["genre"], ["Arts", "Books"])
        self.assertEqual(metadata["hosts"], ["中信出版·大方"])
        self.assertIsNotNone(site.get_item().recent_episodes[0].title)
        self.assertIsNotNone(site.get_item().recent_episodes[0].link)
        self.assertIsNotNone(site.get_item().recent_episodes[0].media_url)

    # @use_local_response
    # def test_scrape_lizhi(self):
    #     t_url = "http://rss.lizhi.fm/rss/14275.xml"
    #     site = SiteManager.get_site_by_url(t_url)
    #     self.assertIsNotNone(site)
    #     site.get_resource_ready()
    #     self.assertEqual(site.ready, True)
    #     metadata = site.resource.metadata
    #     self.assertEqual(metadata["title"], "大内密谈")
    #     self.assertEqual(metadata["genre"], ["other"])
    #     self.assertEqual(metadata["hosts"], ["大内密谈"])
    #     self.assertIsNotNone(site.get_item().recent_episodes[0].title)
    #     self.assertIsNotNone(site.get_item().recent_episodes[0].link)
    #     self.assertIsNotNone(site.get_item().recent_episodes[0].media_url)
