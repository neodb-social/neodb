import pytest

from catalog.common import *
from catalog.common.sites import crawl_related_resources_task


@pytest.mark.django_db(databases="__all__")
class TestDoubanDrama:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        pass

    def test_parse(self):
        t_id = "24849279"
        t_url = "https://www.douban.com/location/drama/24849279/"
        t_url2 = (
            "https://www.douban.com/doubanapp/dispatch?uri=/drama/24849279/&dt_dapp=1"
        )
        p1 = SiteManager.get_site_cls_by_id_type(IdType.DoubanDrama)
        assert p1 is not None
        p1 = SiteManager.get_site_by_url(t_url)
        assert p1 is not None
        assert p1.validate_url(t_url)
        assert p1.id_to_url(t_id) == t_url
        assert p1.url_to_id(t_url) == t_id
        assert p1.url_to_id(t_url2) == t_id

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.douban.com/location/drama/25883969/"
        site = SiteManager.get_site_by_url(t_url)
        resource = site.get_resource_ready()
        item = site.get_item()
        assert item.display_title == "不眠之人·拿破仑"
        assert len(item.localized_title) == 2
        assert item.genre == ["音乐剧"]
        assert item.troupe == ["宝塚歌剧团"]
        assert item.composer == ["ジェラール・プレスギュルヴィック"]

        t_url = "https://www.douban.com/location/drama/20270776/"
        site = SiteManager.get_site_by_url(t_url)
        resource = site.get_resource_ready()
        item = site.get_item()
        assert item.display_title == "相声说垮鬼子们"
        assert item.opening_date == "1997-05"
        assert item.location == ["臺北新舞臺"]

        t_url = "https://www.douban.com/location/drama/24311571/"
        site = SiteManager.get_site_by_url(t_url)
        if site is None:
            raise ValueError()
        resource = site.get_resource_ready()
        item = site.get_item()
        if item is None:
            raise ValueError()
        assert item.orig_title == "Iphigenie auf Tauris"
        assert len(item.localized_title) == 3
        assert item.opening_date == "1974-04-21"
        assert item.choreographer == ["Pina Bausch"]

        t_url = "https://www.douban.com/location/drama/24849279/"
        site = SiteManager.get_site_by_url(t_url)
        assert not site.ready
        resource = site.get_resource_ready()
        assert site.ready
        assert resource.metadata["title"] == "红花侠"
        assert resource.metadata["orig_title"] == "スカーレットピンパーネル"
        item = site.get_item()
        if item is None:
            raise ValueError()
        assert item.display_title == "THE SCARLET PIMPERNEL"
        assert len(item.localized_title) == 3
        assert len(item.display_description) == 545
        assert item.genre == ["音乐剧"]
        # assert item.version == ["08星组公演版", "10年月組公演版", "17年星組公演版", "ュージカル（2017年）版"]
        assert item.director == ["小池修一郎", "小池 修一郎", "石丸さち子"]
        assert item.playwright == [
            "小池修一郎",
            "Baroness Orczy（原作）",
            "小池 修一郎",
        ]
        assert sorted(item.actor, key=lambda a: a["name"]) == [
            {"name": "安蘭けい", "role": ""},
            {"name": "柚希礼音", "role": ""},
            {"name": "遠野あすか", "role": ""},
            {"name": "霧矢大夢", "role": ""},
            {"name": "龍真咲", "role": ""},
        ]
        assert len(resource.related_resources) == 4
        crawl_related_resources_task(resource.id)  # force the async job to run now
        productions = sorted(list(item.productions.all()), key=lambda p: p.opening_date)
        assert len(productions) == 4
        assert productions[3].actor == [
            {"name": "石丸幹二", "role": "パーシー・ブレイクニー"},
            {"name": "石井一孝", "role": "ショーヴラン"},
            {"name": "安蘭けい", "role": "マルグリット・サン・ジュスト"},
            {"name": "上原理生", "role": ""},
            {"name": "泉見洋平", "role": ""},
            {"name": "松下洸平", "role": "アルマン"},
        ]
        assert productions[0].opening_date == "2008-06-20"
        assert productions[0].closing_date == "2008-08-04"
        assert productions[2].opening_date == "2017-03-10"
        assert productions[2].closing_date == "2017-03-17"
        assert productions[3].opening_date == "2017-11-13"
        assert productions[3].closing_date is None
        assert (
            productions[3].display_title
            == "THE SCARLET PIMPERNEL ミュージカル（2017年）版"
        )
        assert len(productions[3].actor) == 6
        assert productions[3].language == ["ja"]
        assert productions[3].opening_date == "2017-11-13"
        assert productions[3].location == ["梅田芸術劇場メインホール"]


@pytest.mark.django_db(databases="__all__")
class TestBangumiDrama:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        pass

    @use_local_response
    def test_scrape(self):
        t_url = "https://bgm.tv/subject/224973"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        item = site.get_item()
        assert item.display_title == "超级弹丸论破2舞台剧~再见了绝望学园~2017"
        assert sorted(item.actor, key=lambda a: a["name"]) == [
            {"name": "伊藤萌々香", "role": None},
            {"name": "横浜流星", "role": None},
            {"name": "鈴木拡樹", "role": None},
        ]
        assert item.language == ["ja"]

        t_url = "https://bgm.tv/subject/442025"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        item = site.get_item()
        assert item.display_title == "LIVE STAGE「ぼっち・ざ・ろっく！」"
        assert item.orig_creator == [
            "はまじあき（芳文社「まんがタイムきららMAX」連載中）／TVアニメ「ぼっち・ざ・ろっく！」"
        ]
        assert item.opening_date == "2023-08-11"
        assert item.closing_date == "2023-08-20"
        assert item.genre == ["舞台演出"]
        assert item.language == ["ja"]
        assert item.playwright == ["山崎彬"]
        assert item.director == ["山崎彬"]
        assert sorted(item.actor, key=lambda a: a["name"]) == [
            {"name": "大森未来衣", "role": None},
            {"name": "大竹美希", "role": None},
            {"name": "守乃まも", "role": None},
            {"name": "小山内花凜", "role": None},
        ]
