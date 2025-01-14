from django.test import TestCase

from catalog.common import *
from catalog.common.sites import crawl_related_resources_task


class DoubanDramaTestCase(TestCase):
    databases = "__all__"

    def setUp(self):
        pass

    def test_parse(self):
        t_id = "24849279"
        t_url = "https://www.douban.com/location/drama/24849279/"
        t_url2 = (
            "https://www.douban.com/doubanapp/dispatch?uri=/drama/24849279/&dt_dapp=1"
        )
        p1 = SiteManager.get_site_cls_by_id_type(IdType.DoubanDrama)
        self.assertIsNotNone(p1)
        p1 = SiteManager.get_site_by_url(t_url)
        self.assertIsNotNone(p1)
        self.assertEqual(p1.validate_url(t_url), True)
        self.assertEqual(p1.id_to_url(t_id), t_url)
        self.assertEqual(p1.url_to_id(t_url), t_id)
        self.assertEqual(p1.url_to_id(t_url2), t_id)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.douban.com/location/drama/25883969/"
        site = SiteManager.get_site_by_url(t_url)
        resource = site.get_resource_ready()
        item = site.get_item()
        self.assertEqual(item.display_title, "不眠之人·拿破仑")
        self.assertEqual(len(item.localized_title), 2)
        self.assertEqual(item.genre, ["音乐剧"])
        self.assertEqual(item.troupe, ["宝塚歌剧团"])
        self.assertEqual(item.composer, ["ジェラール・プレスギュルヴィック"])

        t_url = "https://www.douban.com/location/drama/20270776/"
        site = SiteManager.get_site_by_url(t_url)
        resource = site.get_resource_ready()
        item = site.get_item()
        self.assertEqual(item.display_title, "相声说垮鬼子们")
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
        self.assertEqual(len(item.localized_title), 3)
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
        self.assertEqual(item.display_title, "THE SCARLET PIMPERNEL")
        self.assertEqual(len(item.localized_title), 3)
        self.assertEqual(len(item.display_description), 545)
        self.assertEqual(item.genre, ["音乐剧"])
        # self.assertEqual(
        #     item.version, ["08星组公演版", "10年月組公演版", "17年星組公演版", "ュージカル（2017年）版"]
        # )
        self.assertEqual(item.director, ["小池修一郎", "小池 修一郎", "石丸さち子"])
        self.assertEqual(
            item.playwright, ["小池修一郎", "Baroness Orczy（原作）", "小池 修一郎"]
        )
        self.assertEqual(
            sorted(item.actor, key=lambda a: a["name"]),
            [
                {"name": "安蘭けい", "role": ""},
                {"name": "柚希礼音", "role": ""},
                {"name": "遠野あすか", "role": ""},
                {"name": "霧矢大夢", "role": ""},
                {"name": "龍真咲", "role": ""},
            ],
        )
        self.assertEqual(len(resource.related_resources), 4)
        crawl_related_resources_task(resource.id)  # force the async job to run now
        productions = sorted(list(item.productions.all()), key=lambda p: p.opening_date)
        self.assertEqual(len(productions), 4)
        self.assertEqual(
            productions[3].actor,
            [
                {"name": "石丸幹二", "role": "パーシー・ブレイクニー"},
                {"name": "石井一孝", "role": "ショーヴラン"},
                {"name": "安蘭けい", "role": "マルグリット・サン・ジュスト"},
                {"name": "上原理生", "role": ""},
                {"name": "泉見洋平", "role": ""},
                {"name": "松下洸平", "role": "アルマン"},
            ],
        )
        self.assertEqual(productions[0].opening_date, "2008-06-20")
        self.assertEqual(productions[0].closing_date, "2008-08-04")
        self.assertEqual(productions[2].opening_date, "2017-03-10")
        self.assertEqual(productions[2].closing_date, "2017-03-17")
        self.assertEqual(productions[3].opening_date, "2017-11-13")
        self.assertEqual(productions[3].closing_date, None)
        self.assertEqual(
            productions[3].display_title,
            "THE SCARLET PIMPERNEL ミュージカル（2017年）版",
        )
        self.assertEqual(len(productions[3].actor), 6)
        self.assertEqual(productions[3].language, ["日语"])
        self.assertEqual(productions[3].opening_date, "2017-11-13")
        self.assertEqual(productions[3].location, ["梅田芸術劇場メインホール"])


class BangumiDramaTestCase(TestCase):
    databases = "__all__"

    def setUp(self):
        pass

    @use_local_response
    def test_scrape(self):
        t_url = "https://bgm.tv/subject/224973"
        site = SiteManager.get_site_by_url(t_url)
        resource = site.get_resource_ready()
        item = site.get_item()
        self.assertEqual(item.display_title, "超级弹丸论破2舞台剧~再见了绝望学园~2017")
        self.assertEqual(
            sorted(item.actor, key=lambda a: a["name"]),
            [
                {"name": "伊藤萌々香", "role": None},
                {"name": "横浜流星", "role": None},
                {"name": "鈴木拡樹", "role": None},
            ],
        )
        self.assertEqual(item.language, ["日语"])

        t_url = "https://bgm.tv/subject/442025"
        site = SiteManager.get_site_by_url(t_url)
        resource = site.get_resource_ready()
        item = site.get_item()
        self.assertEqual(item.display_title, "LIVE STAGE「ぼっち・ざ・ろっく！」")
        self.assertEqual(
            item.orig_creator,
            [
                "はまじあき（芳文社「まんがタイムきららMAX」連載中）／TVアニメ「ぼっち・ざ・ろっく！」"
            ],
        )
        self.assertEqual(item.opening_date, "2023-08-11")
        self.assertEqual(item.closing_date, "2023-08-20")
        self.assertEqual(item.genre, ["舞台演出"])
        self.assertEqual(item.language, ["日本语"])
        self.assertEqual(item.playwright, ["山崎彬"])
        self.assertEqual(item.director, ["山崎彬"])
        self.assertEqual(
            sorted(item.actor, key=lambda a: a["name"]),
            [
                {"name": "大森未来衣", "role": None},
                {"name": "大竹美希", "role": None},
                {"name": "守乃まも", "role": None},
                {"name": "小山内花凜", "role": None},
            ],
        )
