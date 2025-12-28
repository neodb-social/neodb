import pytest

from catalog.common import SiteManager, use_local_response
from catalog.models import Edition, ExternalResource, IdType, SiteName, Work
from catalog.models.utils import detect_isbn_asin


@pytest.mark.django_db(databases="__all__")
class TestBook:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        hyperion = Edition.objects.create(
            title="Hyperion", localized_title=[{"lang": "en", "text": "Hyperion"}]
        )
        hyperion.pages = 500
        hyperion.isbn = "9780553283686"
        hyperion.save()
        self.hbla = ExternalResource.objects.create(
            item=hyperion,
            id_type=IdType.Goodreads,
            id_value="77566",
            metadata={
                "localized_title": [
                    {"lang": "en", "text": "Hyperion"},
                    {"lang": "zh", "text": "海伯利安"},
                ]
            },
        )

    def test_url(self):
        hyperion = Edition.objects.get(title="Hyperion")
        hyperion2 = Edition.get_by_url(hyperion.url)
        assert hyperion == hyperion2
        hyperion2 = Edition.get_by_url(hyperion.uuid)
        assert hyperion == hyperion2
        hyperion2 = Edition.get_by_url("test/" + hyperion.uuid + "/test")
        assert hyperion == hyperion2

    def test_properties(self):
        hyperion = Edition.objects.get(title="Hyperion")
        assert hyperion.title == "Hyperion"
        assert hyperion.pages == 500
        assert hyperion.primary_lookup_id_type == IdType.ISBN
        assert hyperion.primary_lookup_id_value == "9780553283686"
        andymion = Edition(title="Andymion", pages=42)
        assert andymion.pages == 42

    def test_lookupids(self):
        hyperion = Edition.objects.get(title="Hyperion")
        hyperion.asin = "B004G60EHS"
        assert hyperion.primary_lookup_id_type == IdType.ASIN
        assert hyperion.primary_lookup_id_value == "B004G60EHS"
        assert hyperion.isbn is None
        assert hyperion.isbn10 is None

    def test_isbn(self):
        t, n = detect_isbn_asin("0553283685")
        assert t == IdType.ISBN
        assert n == "9780553283686"
        t, n = detect_isbn_asin("9780553283686")
        assert t == IdType.ISBN
        t, n = detect_isbn_asin(" b0043M6780")
        assert t == IdType.ASIN

        hyperion = Edition.objects.get(title="Hyperion")
        assert hyperion.isbn == "9780553283686"
        assert hyperion.isbn10 == "0553283685"
        hyperion.isbn10 = "0575099437"
        assert hyperion.isbn == "9780575099432"
        assert hyperion.isbn10 == "0575099437"

    def test_merge_external_resources(self):
        hyperion = Edition.objects.get(title="Hyperion")
        hyperion.merge_data_from_external_resource(self.hbla)
        assert hyperion.localized_title == [{"lang": "en", "text": "Hyperion"}]
        assert hyperion.other_title == ["海伯利安"]

    def test_get_localized_fields_pick_first_match(self):
        edition = Edition.objects.create(
            title="Fallback",
            localized_title=[
                {"lang": "en", "text": "First Title"},
                {"lang": "en", "text": "Second Title"},
            ],
            localized_description=[
                {"lang": "en", "text": "First Description"},
                {"lang": "en", "text": "Second Description"},
            ],
        )
        assert edition.get_localized_title() == "First Title"
        assert edition.get_localized_description() == "First Description"


@pytest.mark.django_db(databases="__all__")
class TestWork:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.hyperion_hardcover = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "Hyperion"}]
        )
        self.hyperion_hardcover.pages = 481
        self.hyperion_hardcover.isbn = "9780385249492"
        self.hyperion_hardcover.save()
        self.hyperion_print = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "Hyperion"}]
        )
        self.hyperion_print.pages = 500
        self.hyperion_print.isbn = "9780553283686"
        self.hyperion_print.save()
        self.hyperion_ebook = Edition(title="Hyperion")
        self.hyperion_ebook.asin = "B0043M6780"
        self.hyperion_ebook.save()
        self.andymion_print = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "Andymion"}], pages=42
        )
        self.hyperion = Work(localized_title=[{"lang": "en", "text": "Hyperion"}])
        self.hyperion.save()

    def test_work(self):
        assert not self.hyperion_print.sibling_items.exists()
        self.hyperion.editions.add(self.hyperion_print)
        assert not self.hyperion_print.sibling_items.exists()

    def test_merge(self):
        title1 = [{"lang": "zh", "text": "z"}]
        title2 = [{"lang": "en", "text": "e"}]
        w1 = Work.objects.create(localized_title=title1)
        w2 = Work.objects.create(localized_title=title2)
        w2.merge_to(w1)
        assert len(w1.localized_title) == 2

    def test_link(self):
        self.hyperion_print.link_to_related_book(self.hyperion_ebook)
        assert self.hyperion_print.sibling_items.exists()
        assert self.hyperion_ebook.sibling_items.exists()
        work = self.hyperion_print.get_work()
        assert work is not None
        assert work.display_title == self.hyperion_print.display_title
        self.hyperion_print.set_work(None)
        assert not self.hyperion_print.sibling_items.exists()
        assert not self.hyperion_ebook.sibling_items.exists()
        self.hyperion_print.link_to_related_book(self.hyperion_ebook)
        assert self.hyperion_print.sibling_items.exists()
        assert self.hyperion_ebook.sibling_items.exists()
        self.hyperion_ebook.set_work(None)
        assert not self.hyperion_print.sibling_items.exists()
        assert not self.hyperion_ebook.sibling_items.exists()

    def test_link3(self):
        self.hyperion_print.link_to_related_book(self.hyperion_ebook)
        self.hyperion_ebook.link_to_related_book(self.hyperion_hardcover)
        self.hyperion_print.link_to_related_book(self.hyperion_hardcover)
        assert self.hyperion_print.get_work() is not None
        work = self.hyperion_ebook.get_work()
        assert work is not None
        assert work.editions.all().count() == 3

    def test_set_parent_item(self):
        work = Work.objects.create(
            localized_title=[{"lang": "en", "text": "Test Work"}]
        )

        assert self.hyperion_print.get_work() is None

        self.hyperion_print.set_parent_item(work)
        assert self.hyperion_print.get_work() == work
        assert self.hyperion_print in work.editions.all()

        self.hyperion_print.set_parent_item(None)
        assert self.hyperion_print.get_work() is None
        assert self.hyperion_print not in work.editions.all()


@pytest.mark.django_db(databases="__all__")
class TestGoodreads:
    def test_parse(self):
        t_type = IdType.Goodreads
        t_id = "77566"
        t_url = "https://www.goodreads.com/zh/book/show/77566.Hyperion"
        t_url2 = "https://www.goodreads.com/book/show/77566"
        p1 = SiteManager.get_site_cls_by_id_type(t_type)
        p2 = SiteManager.get_site_by_url(t_url)
        assert p1 is not None
        assert p1.id_to_url(t_id) == t_url2
        assert p2 is not None
        assert p2.url_to_id(t_url) == t_id

    @use_local_response
    def test_scrape_g(self):
        t_url = "https://www.goodreads.com/book/show/77566.Hyperion"
        t_url2 = "https://www.goodreads.com/book/show/77566"
        isbn = "9780553283686"
        site = SiteManager.get_site_by_url(t_url, False)
        assert site is not None
        assert site.ready is False
        assert site.url == t_url2
        site.get_resource()
        assert site.ready is False
        assert site.resource is not None
        site.get_resource_ready()
        assert site.ready is True
        assert site.resource.metadata.get("title") == "Hyperion"
        assert site.resource.get_all_lookup_ids().get(IdType.ISBN) == isbn
        assert site.resource.required_resources[0]["id_value"] == "1383900"
        edition = Edition.objects.get(
            primary_lookup_id_type=IdType.ISBN, primary_lookup_id_value=isbn
        )
        resource = edition.external_resources.all().first()
        assert resource is not None
        assert resource.id_type == IdType.Goodreads
        assert resource.id_value == "77566"
        assert resource.cover != "/media/item/default.svg"
        assert edition.isbn == "9780553283686"
        assert edition.format == "paperback"
        assert edition.display_title == "Hyperion"

        edition.delete()
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.ready is False
        assert site.url == t_url2
        site.get_resource()
        assert site.ready is True  # previous resource should still exist with data

    @use_local_response
    def test_scrape2(self):
        site = SiteManager.get_site_by_url(
            "https://www.goodreads.com/book/show/13079982-fahrenheit-451"
        )
        assert site is not None
        site.get_resource_ready()
        assert site.resource is not None
        brief = site.resource.metadata.get("brief")
        assert brief is not None
        assert "<br" not in brief
        assert site.resource.has_cover()
        assert isinstance(site.resource.item, Edition)
        assert site.resource.item.has_cover()

    @use_local_response
    def test_asin(self):
        t_url = "https://www.goodreads.com/book/show/45064996-hyperion"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.resource is not None
        assert isinstance(site.resource.item, Edition)
        print(site.resource.id_type, site.resource.id_value)
        print(site.resource.other_lookup_ids)
        assert site.resource.item.display_title == "Hyperion"
        assert site.resource.item.asin == "B004G60EHS"

    @use_local_response
    def test_work_g(self):
        url = "https://www.goodreads.com/work/editions/153313"
        site = SiteManager.get_site_by_url(url)
        assert site is not None
        p = site.get_resource_ready()
        assert p is not None
        assert p.item is not None
        assert p.item.display_title == "1984"
        url1 = "https://www.goodreads.com/book/show/3597767-rok-1984"
        url2 = "https://www.goodreads.com/book/show/40961427-1984"
        site1 = SiteManager.get_site_by_url(url1)
        assert site1 is not None
        p1 = site1.get_resource_ready()
        assert p1 is not None
        assert p1.item is not None
        site2 = SiteManager.get_site_by_url(url2)
        assert site2 is not None
        p2 = site2.get_resource_ready()
        assert p2 is not None
        assert p2.item is not None
        assert isinstance(p1.item, Edition)
        w1 = p1.item.get_work()
        assert isinstance(p2.item, Edition)
        w2 = p2.item.get_work()
        assert w1 == w2


@pytest.mark.django_db(databases="__all__")
class TestGoogleBooks:
    def test_parse(self):
        t_type = IdType.GoogleBooks
        t_id = "hV--zQEACAAJ"
        t_url = "https://books.google.com.bn/books?id=hV--zQEACAAJ&hl=ms"
        t_url2 = "https://books.google.com/books?id=hV--zQEACAAJ"
        p1 = SiteManager.get_site_by_url(t_url)
        p2 = SiteManager.get_site_by_url(t_url2)
        assert p1 is not None
        assert p1.url == t_url2
        assert p1.ID_TYPE == t_type
        assert p1.id_value == t_id
        assert p2 is not None
        assert p2.url == t_url2

    @use_local_response
    def test_scrape(self):
        t_url = "https://books.google.com.bn/books?id=hV--zQEACAAJ"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.ready is False
        site.get_resource_ready()
        assert site.ready is True
        assert site.resource is not None
        assert site.resource.metadata.get("title") == "1984 Nineteen Eighty-Four"
        assert site.resource.metadata.get("isbn") == "9781847498571"
        assert site.resource.id_type == IdType.GoogleBooks
        assert site.resource.id_value == "hV--zQEACAAJ"
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Edition)
        assert site.resource.item.isbn == "9781847498571"
        assert site.resource.item.localized_title == [
            {"lang": "en", "text": "1984 Nineteen Eighty-Four"}
        ]
        assert site.resource.item.display_title == "1984 Nineteen Eighty-Four"


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
        assert p2 is not None
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
        assert site.resource is not None
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
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Edition)
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
        assert p1 is not None
        assert p2 is not None
        assert p1.url == t_url2
        assert p1.ID_TYPE == t_type
        assert p1.id_value == t_id
        assert p2.url == t_url2

    @use_local_response
    def test_scrape(self):
        t_url = "https://book.douban.com/subject/35902899/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.ready is False
        assert site.get_item() is None
        site.get_resource_ready()
        assert site.ready is True
        assert site.resource is not None
        assert site.resource.site_name == SiteName.Douban
        assert site.resource.metadata.get("title") == "1984 Nineteen Eighty-Four"
        assert site.resource.metadata.get("isbn") == "9781847498571"
        assert site.resource.id_type == IdType.DoubanBook
        assert site.resource.id_value == "35902899"
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Edition)
        assert site.resource.item.isbn == "9781847498571"
        assert isinstance(site.resource.item, Edition)
        assert site.resource.item.format == "paperback"
        assert site.resource.item.display_title == "1984 Nineteen Eighty-Four"

    @use_local_response
    def test_publisher(self):
        t_url = "https://book.douban.com/subject/35902899/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        res = site.get_resource_ready()
        assert res is not None
        assert res.metadata.get("pub_house") == "Alma Classics"
        t_url = "https://book.douban.com/subject/1089243/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        res = site.get_resource_ready()
        assert res is not None
        assert res.metadata.get("pub_house") == "花城出版社"

    @use_local_response
    def test_work(self):
        url1 = "https://book.douban.com/subject/1089243/"
        url2 = "https://book.douban.com/subject/2037260/"
        site1 = SiteManager.get_site_by_url(url1)
        assert site1 is not None
        p1 = site1.get_resource_ready()
        assert p1 is not None
        assert p1.item is not None
        site2 = SiteManager.get_site_by_url(url2)
        assert site2 is not None
        p2 = site2.get_resource_ready()
        assert p2 is not None
        assert isinstance(p1.item, Edition)
        w1 = p1.item.get_work()
        assert isinstance(p2.item, Edition)
        assert p1.item != p2.item
        w2 = p2.item.get_work()
        assert isinstance(w1, Work)
        assert isinstance(w2, Work)
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
        assert p1 is not None
        assert p2 is not None
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
        assert site is not None
        assert site.ready is False
        site.get_resource_ready()
        assert site.ready is True
        assert site.resource is not None
        assert site.resource.site_name == SiteName.AO3
        assert site.resource.id_type == IdType.AO3
        assert site.resource.id_value == "2080878"
        assert site.resource.item is not None
        assert site.resource.item.display_title == "I Am Groot"
        assert isinstance(site.resource.item, Edition)
        assert isinstance(site.resource.item, Edition)
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
        assert p1 is not None
        assert p2 is not None
        assert p1.url == t_url2
        assert p1.ID_TYPE == t_type
        assert p1.id_value == t_id
        assert p2.url == t_url2

    @use_local_response
    def test_scrape(self):
        t_url = "https://book.qidian.com/info/1010868264/"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.ready is False
        site.get_resource_ready()
        assert site.ready is True
        assert site.resource is not None
        assert site.resource.site_name == SiteName.Qidian
        assert site.resource.id_type == IdType.Qidian
        assert site.resource.id_value == "1010868264"
        assert site.resource.item is not None
        assert site.resource.item.display_title == "诡秘之主"
        assert isinstance(site.resource.item, Edition)
        assert site.resource.item.author[0] == "爱潜水的乌贼"
