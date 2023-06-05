from django.test import TestCase
from catalog.common import *
from catalog.common.sites import crawl_related_resources_task


class DoubanDramaTestCase(TestCase):
    def setUp(self):
        pass

    def test_parse(self):
        t_id = "24849279"
        t_url = "https://www.douban.com/location/drama/24849279/"
        p1 = SiteManager.get_site_cls_by_id_type(IdType.DoubanDrama)
        self.assertIsNotNone(p1)
        p1 = SiteManager.get_site_by_url(t_url)
        self.assertIsNotNone(p1)
        self.assertEqual(p1.validate_url(t_url), True)
        self.assertEqual(p1.id_to_url(t_id), t_url)
        self.assertEqual(p1.url_to_id(t_url), t_id)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.douban.com/location/drama/25883969/"
        site = SiteManager.get_site_by_url(t_url)
        resource = site.get_resource_ready()
        item = site.get_item()
        self.assertEqual(item.title, "不眠之人·拿破仑")
        self.assertEqual(item.other_title, ["眠らない男・ナポレオン　―愛と栄光の涯（はて）に―"])
        self.assertEqual(item.genre, ["音乐剧"])
        self.assertEqual(item.troupe, ["宝塚歌剧团"])
        self.assertEqual(item.composer, ["ジェラール・プレスギュルヴィック"])

        t_url = "https://www.douban.com/location/drama/20270776/"
        site = SiteManager.get_site_by_url(t_url)
        resource = site.get_resource_ready()
        item = site.get_item()
        self.assertEqual(item.title, "相声说垮鬼子们")
        self.assertEqual(item.opening_date, "1997-05")
        self.assertEqual(item.location, ["臺北新舞臺"])

        t_url = "https://www.douban.com/location/drama/24311571/"
        site = SiteManager.get_site_by_url(t_url)
        if site is None:
            raise ValueError()
        resource = site.get_resource_ready()
        item = site.get_item()
        if item is None:
            raise ValueError()
        self.assertEqual(item.orig_title, "Iphigenie auf Tauris")
        self.assertEqual(sorted(item.other_title), ["死而复生的伊菲格尼"])
        self.assertEqual(item.opening_date, "1974-04-21")
        self.assertEqual(item.choreographer, ["Pina Bausch"])

        t_url = "https://www.douban.com/location/drama/24849279/"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        resource = site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(resource.metadata["title"], "红花侠")
        self.assertEqual(resource.metadata["orig_title"], "スカーレットピンパーネル")
        item = site.get_item()
        if item is None:
            raise ValueError()
        self.assertEqual(item.title, "红花侠")
        self.assertEqual(sorted(item.other_title), ["THE SCARLET PIMPERNEL"])
        self.assertEqual(len(item.brief), 545)
        self.assertEqual(item.genre, ["音乐剧"])
        # self.assertEqual(
        #     item.version, ["08星组公演版", "10年月組公演版", "17年星組公演版", "ュージカル（2017年）版"]
        # )
        self.assertEqual(item.director, ["小池修一郎", "小池 修一郎", "石丸さち子"])
        self.assertEqual(item.playwright, ["小池修一郎", "Baroness Orczy（原作）", "小池 修一郎"])
        self.assertEqual(item.performer, ["安蘭けい", "柚希礼音", "遠野あすか", "霧矢大夢", "龍真咲"])
        self.assertEqual(len(resource.related_resources), 4)
        crawl_related_resources_task(resource.id)  # force the async job to run now
        productions = list(item.productions.all().order_by("title"))
        self.assertEqual(len(productions), 4)
        self.assertEqual(productions[0].opening_date, "2008-06-20")
        self.assertEqual(productions[0].closing_date, "2008-08-04")
        self.assertEqual(productions[2].opening_date, "2017-03-10")
        self.assertEqual(productions[2].closing_date, "2017-03-17")
        self.assertEqual(productions[3].opening_date, "2017-11-13")
        self.assertEqual(productions[3].closing_date, None)
        self.assertEqual(productions[3].title, "ミュージカル（2017年）版")
        self.assertEqual(len(productions[3].performer), 6)
        self.assertEqual(productions[3].language, ["日语"])
        self.assertEqual(productions[3].opening_date, "2017-11-13")
        self.assertEqual(productions[3].location, ["梅田芸術劇場メインホール"])
