from django.test import TestCase

from catalog.book.models import *
from catalog.book.utils import *
from catalog.common import *


class BookTestCase(TestCase):
    databases = "__all__"

    def setUp(self):
        hyperion = Edition.objects.create(title="Hyperion")
        hyperion.pages = 500
        hyperion.isbn = "9780553283686"
        hyperion.save()
        # hyperion.isbn10 = '0553283685'

    def test_url(self):
        hyperion = Edition.objects.get(title="Hyperion")
        hyperion2 = Edition.get_by_url(hyperion.url)
        self.assertEqual(hyperion, hyperion2)
        hyperion2 = Edition.get_by_url(hyperion.uuid)
        self.assertEqual(hyperion, hyperion2)
        hyperion2 = Edition.get_by_url("test/" + hyperion.uuid + "/test")
        self.assertEqual(hyperion, hyperion2)

    def test_properties(self):
        hyperion = Edition.objects.get(title="Hyperion")
        self.assertEqual(hyperion.title, "Hyperion")
        self.assertEqual(hyperion.pages, 500)
        self.assertEqual(hyperion.primary_lookup_id_type, IdType.ISBN)
        self.assertEqual(hyperion.primary_lookup_id_value, "9780553283686")
        andymion = Edition(title="Andymion", pages=42)
        self.assertEqual(andymion.pages, 42)

    def test_lookupids(self):
        hyperion = Edition.objects.get(title="Hyperion")
        hyperion.asin = "B004G60EHS"
        self.assertEqual(hyperion.primary_lookup_id_type, IdType.ASIN)
        self.assertEqual(hyperion.primary_lookup_id_value, "B004G60EHS")
        self.assertEqual(hyperion.isbn, None)
        self.assertEqual(hyperion.isbn10, None)

    def test_isbn(self):
        t, n = detect_isbn_asin("0553283685")
        self.assertEqual(t, IdType.ISBN)
        self.assertEqual(n, "9780553283686")
        t, n = detect_isbn_asin("9780553283686")
        self.assertEqual(t, IdType.ISBN)
        t, n = detect_isbn_asin(" b0043M6780")
        self.assertEqual(t, IdType.ASIN)

        hyperion = Edition.objects.get(title="Hyperion")
        self.assertEqual(hyperion.isbn, "9780553283686")
        self.assertEqual(hyperion.isbn10, "0553283685")
        hyperion.isbn10 = "0575099437"
        self.assertEqual(hyperion.isbn, "9780575099432")
        self.assertEqual(hyperion.isbn10, "0575099437")


class WorkTestCase(TestCase):
    databases = "__all__"

    def setUp(self):
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
        # serie = Serie(title="Hyperion Cantos")
        self.hyperion = Work(localized_title=[{"lang": "en", "text": "Hyperion"}])
        self.hyperion.save()

    def test_work(self):
        self.assertFalse(self.hyperion_print.has_related_books())
        self.hyperion.editions.add(self.hyperion_print)
        self.assertFalse(self.hyperion_print.has_related_books())

    def test_merge(self):
        title1 = [{"lang": "zh", "text": "z"}]
        title2 = [{"lang": "en", "text": "e"}]
        w1 = Work.objects.create(localized_title=title1)
        w2 = Work.objects.create(localized_title=title2)
        w2.merge_to(w1)
        self.assertEqual(len(w1.localized_title), 2)

    def test_link(self):
        self.hyperion_print.link_to_related_book(self.hyperion_ebook)
        self.assertTrue(self.hyperion_print.has_related_books())
        self.assertTrue(self.hyperion_ebook.has_related_books())
        self.assertTrue(self.hyperion_print.has_works())
        self.assertEqual(
            self.hyperion_print.works.first().display_title,
            self.hyperion_print.display_title,
        )
        self.hyperion_print.unlink_from_all_works()
        self.assertFalse(self.hyperion_print.has_related_books())
        self.assertFalse(self.hyperion_ebook.has_related_books())
        self.hyperion_print.link_to_related_book(self.hyperion_ebook)
        self.assertTrue(self.hyperion_print.has_related_books())
        self.assertTrue(self.hyperion_ebook.has_related_books())
        self.hyperion_ebook.unlink_from_all_works()
        self.assertFalse(self.hyperion_print.has_related_books())
        self.assertFalse(self.hyperion_ebook.has_related_books())

    def test_link3(self):
        self.hyperion_print.link_to_related_book(self.hyperion_ebook)
        self.hyperion_ebook.link_to_related_book(self.hyperion_hardcover)
        self.hyperion_print.link_to_related_book(self.hyperion_hardcover)
        self.assertTrue(self.hyperion_print.has_works())
        self.assertEqual(self.hyperion_print.works.all().count(), 1)
        self.assertEqual(
            self.hyperion_ebook.works.all().first().editions.all().count(), 3
        )


class GoodreadsTestCase(TestCase):
    databases = "__all__"

    def setUp(self):
        pass

    def test_parse(self):
        t_type = IdType.Goodreads
        t_id = "77566"
        t_url = "https://www.goodreads.com/zh/book/show/77566.Hyperion"
        t_url2 = "https://www.goodreads.com/book/show/77566"
        p1 = SiteManager.get_site_cls_by_id_type(t_type)
        p2 = SiteManager.get_site_by_url(t_url)
        self.assertEqual(p1.id_to_url(t_id), t_url2)
        self.assertEqual(p2.url_to_id(t_url), t_id)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.goodreads.com/book/show/77566.Hyperion"
        t_url2 = "https://www.goodreads.com/book/show/77566"
        isbn = "9780553283686"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        self.assertEqual(site.url, t_url2)
        site.get_resource()
        self.assertEqual(site.ready, False)
        self.assertIsNotNone(site.resource)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.metadata.get("title"), "Hyperion")
        self.assertEqual(site.resource.get_all_lookup_ids().get(IdType.ISBN), isbn)
        self.assertEqual(site.resource.required_resources[0]["id_value"], "1383900")
        edition = Edition.objects.get(
            primary_lookup_id_type=IdType.ISBN, primary_lookup_id_value=isbn
        )
        resource = edition.external_resources.all().first()
        self.assertEqual(resource.id_type, IdType.Goodreads)
        self.assertEqual(resource.id_value, "77566")
        self.assertNotEqual(resource.cover, "/media/item/default.svg")
        self.assertEqual(edition.isbn, "9780553283686")
        self.assertEqual(edition.format, "paperback")
        self.assertEqual(edition.display_title, "Hyperion")

        edition.delete()
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        self.assertEqual(site.url, t_url2)
        site.get_resource()
        self.assertEqual(
            site.ready, True, "previous resource should still exist with data"
        )

    @use_local_response
    def test_scrape2(self):
        site = SiteManager.get_site_by_url(
            "https://www.goodreads.com/book/show/13079982-fahrenheit-451"
        )
        site.get_resource_ready()
        self.assertNotIn("<br", site.resource.metadata.get("brief"))

    @use_local_response
    def test_asin(self):
        t_url = "https://www.goodreads.com/book/show/45064996-hyperion"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        self.assertEqual(site.resource.item.display_title, "Hyperion")
        self.assertEqual(site.resource.item.asin, "B004G60EHS")

    @use_local_response
    def test_work(self):
        url = "https://www.goodreads.com/work/editions/153313"
        p = SiteManager.get_site_by_url(url).get_resource_ready()
        self.assertEqual(p.item.display_title, "1984")
        url1 = "https://www.goodreads.com/book/show/3597767-rok-1984"
        url2 = "https://www.goodreads.com/book/show/40961427-1984"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        w1 = p1.item.works.all().first()
        w2 = p2.item.works.all().first()
        self.assertEqual(w1, w2)


class GoogleBooksTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_type = IdType.GoogleBooks
        t_id = "hV--zQEACAAJ"
        t_url = "https://books.google.com.bn/books?id=hV--zQEACAAJ&hl=ms"
        t_url2 = "https://books.google.com/books?id=hV--zQEACAAJ"
        p1 = SiteManager.get_site_by_url(t_url)
        p2 = SiteManager.get_site_by_url(t_url2)
        self.assertIsNotNone(p1)
        self.assertEqual(p1.url, t_url2)
        self.assertEqual(p1.ID_TYPE, t_type)
        self.assertEqual(p1.id_value, t_id)
        self.assertEqual(p2.url, t_url2)

    @use_local_response
    def test_scrape(self):
        t_url = "https://books.google.com.bn/books?id=hV--zQEACAAJ"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(
            site.resource.metadata.get("title"), "1984 Nineteen Eighty-Four"
        )
        self.assertEqual(site.resource.metadata.get("isbn"), "9781847498571")
        self.assertEqual(site.resource.id_type, IdType.GoogleBooks)
        self.assertEqual(site.resource.id_value, "hV--zQEACAAJ")
        self.assertEqual(site.resource.item.isbn, "9781847498571")
        self.assertEqual(
            site.resource.item.localized_title,
            [{"lang": "en", "text": "1984 Nineteen Eighty-Four"}],
        )
        self.assertEqual(site.resource.item.display_title, "1984 Nineteen Eighty-Four")


class BooksTWTestCase(TestCase):
    databases = "__all__"

    def test_parse(self):
        t_type = IdType.BooksTW
        t_id = "0010947886"
        t_url = "https://www.books.com.tw/products/0010947886?loc=P_br_60nq68yhb_D_2aabdc_B_1"
        t_url2 = "https://www.books.com.tw/products/0010947886"
        p1 = SiteManager.get_site_by_url(t_url)
        p2 = SiteManager.get_site_by_url(t_url2)
        self.assertIsNotNone(p1)
        self.assertEqual(p1.url, t_url2)
        self.assertEqual(p1.ID_TYPE, t_type)
        self.assertEqual(p1.id_value, t_id)
        self.assertEqual(p2.url, t_url2)

    @use_local_response
    def test_scrape(self):
        t_url = "https://www.books.com.tw/products/0010947886"
        site = SiteManager.get_site_by_url(t_url)
        self.assertIsNotNone(site)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(
            site.resource.metadata.get("title"),
            "阿拉伯人三千年：從民族、部落、語言、文化、宗教到帝國，綜覽阿拉伯世界的崛起、衰落與再興",
        )
        self.assertEqual(
            site.resource.metadata.get("orig_title"),
            "Arabs: A 3000-Year History of Peoples, Tribes and Empires",
        )
        self.assertEqual(site.resource.metadata.get("isbn"), "9786263152236")
        self.assertEqual(site.resource.metadata.get("author"), ["Tim Mackintosh-Smith"])
        self.assertEqual(site.resource.metadata.get("translator"), ["吳莉君"])
        self.assertEqual(site.resource.metadata.get("language"), ["繁體中文"])
        self.assertEqual(site.resource.metadata.get("pub_house"), "臉譜")
        self.assertEqual(site.resource.metadata.get("pub_year"), 2023)
        self.assertEqual(site.resource.metadata.get("pub_month"), 2)
        self.assertEqual(site.resource.metadata.get("binding"), "平裝")
        self.assertEqual(site.resource.metadata.get("pages"), 792)
        self.assertEqual(site.resource.metadata.get("price"), "1050 NTD")
        self.assertEqual(site.resource.id_type, IdType.BooksTW)
        self.assertEqual(site.resource.id_value, "0010947886")
        self.assertEqual(site.resource.item.isbn, "9786263152236")
        self.assertEqual(site.resource.item.format, "paperback")
        self.assertEqual(
            site.resource.item.display_title,
            "阿拉伯人三千年：從民族、部落、語言、文化、宗教到帝國，綜覽阿拉伯世界的崛起、衰落與再興",
        )


class DoubanBookTestCase(TestCase):
    databases = "__all__"

    def setUp(self):
        pass

    def test_parse(self):
        t_type = IdType.DoubanBook
        t_id = "35902899"
        t_url = "https://m.douban.com/book/subject/35902899/"
        t_url2 = "https://book.douban.com/subject/35902899/"
        p1 = SiteManager.get_site_by_url(t_url)
        p2 = SiteManager.get_site_by_url(t_url2)
        self.assertEqual(p1.url, t_url2)
        self.assertEqual(p1.ID_TYPE, t_type)
        self.assertEqual(p1.id_value, t_id)
        self.assertEqual(p2.url, t_url2)

    @use_local_response
    def test_scrape(self):
        t_url = "https://book.douban.com/subject/35902899/"
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        site.get_resource_ready()
        self.assertEqual(site.ready, True)
        self.assertEqual(site.resource.site_name, SiteName.Douban)
        self.assertEqual(
            site.resource.metadata.get("title"), "1984 Nineteen Eighty-Four"
        )
        self.assertEqual(site.resource.metadata.get("isbn"), "9781847498571")
        self.assertEqual(site.resource.id_type, IdType.DoubanBook)
        self.assertEqual(site.resource.id_value, "35902899")
        self.assertEqual(site.resource.item.isbn, "9781847498571")
        self.assertEqual(site.resource.item.format, "paperback")
        self.assertEqual(site.resource.item.display_title, "1984 Nineteen Eighty-Four")

    @use_local_response
    def test_publisher(self):
        t_url = "https://book.douban.com/subject/35902899/"
        site = SiteManager.get_site_by_url(t_url)
        res = site.get_resource_ready()
        self.assertEqual(res.metadata.get("pub_house"), "Alma Classics")
        t_url = "https://book.douban.com/subject/1089243/"
        site = SiteManager.get_site_by_url(t_url)
        res = site.get_resource_ready()
        self.assertEqual(res.metadata.get("pub_house"), "花城出版社")

    @use_local_response
    def test_work(self):
        # url = 'https://www.goodreads.com/work/editions/153313'
        url1 = "https://book.douban.com/subject/1089243/"
        url2 = "https://book.douban.com/subject/2037260/"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        w1 = p1.item.works.all().first()
        w2 = p2.item.works.all().first()
        self.assertEqual(w1.display_title, "黄金时代")
        self.assertEqual(w2.display_title, "黄金时代")
        self.assertEqual(w1, w2)
        editions = sorted(list(w1.editions.all()), key=lambda e: e.display_title)
        self.assertEqual(len(editions), 2)
        self.assertEqual(editions[0].display_title, "Wang in Love and Bondage")
        self.assertEqual(editions[1].display_title, "黄金时代")


class MultiBookSitesTestCase(TestCase):
    databases = "__all__"

    @use_local_response
    def test_editions(self):
        # isbn = '9781847498571'
        url1 = "https://www.goodreads.com/book/show/56821625-1984"
        url2 = "https://book.douban.com/subject/35902899/"
        url3 = "https://books.google.com/books?id=hV--zQEACAAJ"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        p3 = SiteManager.get_site_by_url(url3).get_resource_ready()
        self.assertEqual(p1.item.id, p2.item.id)
        self.assertEqual(p2.item.id, p3.item.id)

    @use_local_response
    def test_works(self):
        # url1 and url4 has same ISBN, hence they share same Edition instance, which belongs to 2 Work instances
        url1 = "https://book.douban.com/subject/1089243/"
        url2 = "https://book.douban.com/subject/2037260/"
        url3 = "https://www.goodreads.com/book/show/59952545-golden-age"
        url4 = "https://www.goodreads.com/book/show/11798823"
        p1 = SiteManager.get_site_by_url(
            url1
        ).get_resource_ready()  # lxml bug may break this
        w1 = p1.item.works.all().first()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        w2 = p2.item.works.all().first()
        self.assertEqual(w1, w2)
        self.assertEqual(p1.item.works.all().count(), 1)
        p3 = SiteManager.get_site_by_url(url3).get_resource_ready()
        w3 = p3.item.works.all().first()
        self.assertNotEqual(w3, w2)
        p4 = SiteManager.get_site_by_url(url4).get_resource_ready()
        self.assertEqual(p4.item.id, p1.item.id)
        self.assertEqual(p4.item.works.all().count(), 2)
        self.assertEqual(p1.item.works.all().count(), 2)
        w2e = sorted(list(w2.editions.all()), key=lambda e: e.display_title)
        self.assertEqual(len(w2e), 2)
        self.assertEqual(w2e[0].display_title, "Wang in Love and Bondage")
        self.assertEqual(w2e[1].display_title, "黄金时代")
        w3e = sorted(list(w3.editions.all()), key=lambda e: e.display_title)
        self.assertEqual(len(w3e), 2)
        self.assertEqual(w3e[0].display_title, "Golden Age: A Novel")
        self.assertEqual(w3e[1].display_title, "黄金时代")
        e = Edition.objects.get(primary_lookup_id_value=9781662601217)
        self.assertEqual(e.display_title, "Golden Age: A Novel")

    @use_local_response
    def test_works_merge(self):
        # url1 and url4 has same ISBN, hence they share same Edition instance, which belongs to 2 Work instances
        url1 = "https://book.douban.com/subject/1089243/"
        url2 = "https://book.douban.com/subject/2037260/"
        url3 = "https://www.goodreads.com/book/show/59952545-golden-age"
        url4 = "https://www.goodreads.com/book/show/11798823"
        p1 = SiteManager.get_site_by_url(
            url1
        ).get_resource_ready()  # lxml bug may break this
        w1 = p1.item.works.all().first()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        w2 = p2.item.works.all().first()
        self.assertEqual(w1, w2)
        self.assertEqual(p1.item.works.all().count(), 1)
        p3 = SiteManager.get_site_by_url(url3).get_resource_ready()
        w3 = p3.item.works.all().first()
        self.assertNotEqual(w3, w2)
        self.assertEqual(w2.external_resources.all().count(), 1)
        self.assertEqual(w3.external_resources.all().count(), 1)
        w3.merge_to(w2)
        self.assertEqual(w2.external_resources.all().count(), 2)
        self.assertEqual(w3.external_resources.all().count(), 0)
        self.assertEqual(w2.editions.all().count(), 3)
        self.assertEqual(w3.editions.all().count(), 0)
        p4 = SiteManager.get_site_by_url(url4).get_resource_ready()
        self.assertEqual(p4.item.id, p1.item.id)
        self.assertEqual(p4.item.works.all().count(), 1)
        self.assertEqual(p1.item.works.all().count(), 1)
        w2e = sorted(list(w2.editions.all()), key=lambda e: e.display_title)
        self.assertEqual(len(w2e), 3)
        self.assertEqual(w2e[0].display_title, "Golden Age: A Novel")
        self.assertEqual(w2e[1].display_title, "Wang in Love and Bondage")
        self.assertEqual(w2e[2].display_title, "黄金时代")
        w3e = w3.editions.all().order_by("title")
        self.assertEqual(w3e.count(), 0)
        e = Edition.objects.get(primary_lookup_id_value=9781662601217)
        self.assertEqual(e.display_title, "Golden Age: A Novel")
        w2e[1].delete()
        self.assertEqual(w2.editions.all().count(), 2)
        w2.editions.all().delete()
        self.assertEqual(p1.item.works.all().count(), 0)
