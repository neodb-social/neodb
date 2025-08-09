import pytest

from catalog.common import SiteManager, use_local_response
from catalog.models import IdType, SiteName


@pytest.mark.django_db(databases="__all__")
class TestBooksTW:
    def test_parse(self):
        t_type = IdType.BooksTW
        t_id = "0010947886"
        t_url = "https://www.books.com.tw/products/0010947886?loc=P_br_60nq68yhb_D_2aabdc_B_1"
        t_url2 = "https://www.books.com.tw/products/0010947886"
        p1 = SiteManager.get_site_by_url(t_url)
        p2 = SiteManager.get_site_by_url(t_url2)
        assert p1 is not None
        assert p1.url == t_url2
        assert p1.ID_TYPE == t_type
        assert p1.id_value == t_id
        assert p2.url == t_url2

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.books.com.tw/products/0010947886"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.ready is False
        site.get_resource_ready()
        assert site.ready is True
        assert (
            site.resource.metadata.get("title")
            == "阿拉伯人三千年：從民族、部落、語言、文化、宗教到帝國，綜覽阿拉伯世界的崛起、衰落與再興"
        )
        assert (
            site.resource.metadata.get("orig_title")
            == "Arabs: A 3000-Year History of Peoples, Tribes and Empires"
        )
        assert site.resource.metadata.get("isbn") == "9786263152236"
        assert site.resource.metadata.get("author") == ["Tim Mackintosh-Smith"]
        assert site.resource.metadata.get("translator") == ["吳莉君"]
        assert site.resource.metadata.get("language") == ["繁體中文"]
        assert site.resource.metadata.get("pub_house") == "臉譜"
        assert site.resource.metadata.get("pub_year") == 2023
        assert site.resource.metadata.get("pub_month") == 2
        assert site.resource.metadata.get("binding") == "平裝"
        assert site.resource.metadata.get("pages") == 792
        assert site.resource.metadata.get("price") == "1050 NTD"
        assert site.resource.id_type == IdType.BooksTW
        assert site.resource.id_value == "0010947886"
        assert site.resource.item.isbn == "9786263152236"
        assert site.resource.item.format == "paperback"
        assert (
            site.resource.item.display_title
            == "阿拉伯人三千年：從民族、部落、語言、文化、宗教到帝國，綜覽阿拉伯世界的崛起、衰落與再興"
        )
        assert site.resource.item.language == ["zh-tw"]


@pytest.mark.django_db(databases="__all__")
class TestDoubanBook:
    def test_parse(self):
        t_type = IdType.DoubanBook
        t_id = "35902899"
        t_url = "https://m.douban.com/book/subject/35902899/"
        t_url2 = "https://book.douban.com/subject/35902899/"
        p1 = SiteManager.get_site_by_url(t_url)
        p2 = SiteManager.get_site_by_url(t_url2)
        assert p1.url == t_url2
        assert p1.ID_TYPE == t_type
        assert p1.id_value == t_id
        assert p2.url == t_url2

    @use_local_response
    def test_scrape(self):
        t_url = "https://book.douban.com/subject/35902899/"
        site = SiteManager.get_site_by_url(t_url)
        assert site.ready is False
        site.get_resource_ready()
        assert site.ready is True
        assert site.resource.site_name == SiteName.Douban
        assert site.resource.metadata.get("title") == "1984 Nineteen Eighty-Four"
        assert site.resource.metadata.get("isbn") == "9781847498571"
        assert site.resource.id_type == IdType.DoubanBook
        assert site.resource.id_value == "35902899"
        assert site.resource.item.isbn == "9781847498571"
        assert site.resource.item.format == "paperback"
        assert site.resource.item.display_title == "1984 Nineteen Eighty-Four"

    @use_local_response
    def test_publisher(self):
        t_url = "https://book.douban.com/subject/35902899/"
        site = SiteManager.get_site_by_url(t_url)
        res = site.get_resource_ready()
        assert res.metadata.get("pub_house") == "Alma Classics"
        t_url = "https://book.douban.com/subject/1089243/"
        site = SiteManager.get_site_by_url(t_url)
        res = site.get_resource_ready()
        assert res.metadata.get("pub_house") == "花城出版社"

    @use_local_response
    def test_work(self):
        url1 = "https://book.douban.com/subject/1089243/"
        url2 = "https://book.douban.com/subject/2037260/"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        w1 = p1.item.get_work()
        w2 = p2.item.get_work()
        assert w1.display_title == "黄金时代"
        assert w2.display_title == "黄金时代"
        assert w1 == w2
        editions = sorted(list(w1.editions.all()), key=lambda e: e.display_title)
        assert len(editions) == 2
        assert editions[0].display_title == "Wang in Love and Bondage"
        assert editions[1].display_title == "黄金时代"


@pytest.mark.django_db(databases="__all__")
class TestAO3:
    def test_parse(self):
        t_type = IdType.AO3
        t_id = "2080878"
        t_url = "https://archiveofourown.org/works/2080878"
        t_url2 = "https://archiveofourown.org/works/2080878?test"
        p1 = SiteManager.get_site_by_url(t_url)
        p2 = SiteManager.get_site_by_url(t_url2)
        assert p1.url == t_url
        assert p1.ID_TYPE == t_type
        assert p1.id_value == t_id
        assert p2.url == t_url
        assert p2.ID_TYPE == t_type
        assert p2.id_value == t_id

    @use_local_response
    def test_scrape(self):
        t_url = "https://archiveofourown.org/works/2080878"
        site = SiteManager.get_site_by_url(t_url)
        assert site.ready is False
        site.get_resource_ready()
        assert site.ready is True
        assert site.resource.site_name == SiteName.AO3
        assert site.resource.id_type == IdType.AO3
        assert site.resource.id_value == "2080878"
        assert site.resource.item.display_title == "I Am Groot"
        assert site.resource.item.author[0] == "sherlocksmyth"


@pytest.mark.django_db(databases="__all__")
class TestQidian:
    def test_parse(self):
        t_type = IdType.Qidian
        t_id = "1010868264"
        t_url = "https://www.qidian.com/book/1010868264/"
        t_url2 = "https://book.qidian.com/info/1010868264/"
        p1 = SiteManager.get_site_by_url(t_url)
        p2 = SiteManager.get_site_by_url(t_url2)
        assert p1.url == t_url2
        assert p1.ID_TYPE == t_type
        assert p1.id_value == t_id
        assert p2.url == t_url2

    @use_local_response
    def test_scrape(self):
        t_url = "https://book.qidian.com/info/1010868264/"
        site = SiteManager.get_site_by_url(t_url)
        assert site.ready is False
        site.get_resource_ready()
        assert site.ready is True
        assert site.resource.site_name == SiteName.Qidian
        assert site.resource.id_type == IdType.Qidian
        assert site.resource.id_value == "1010868264"
        assert site.resource.item.display_title == "诡秘之主"
        assert site.resource.item.author[0] == "爱潜水的乌贼"
