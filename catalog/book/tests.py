from django.test import TestCase
from catalog.book.models import *
from catalog.book.utils import *
from catalog.common import *


class BookTestCase(TestCase):
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

    def test_work(self):
        hyperion_print = Edition.objects.get(title="Hyperion")
        hyperion_ebook = Edition(title="Hyperion")
        hyperion_ebook.save()
        hyperion_ebook.asin = "B0043M6780"
        hyperion = Work(title="Hyperion")
        hyperion.save()
        hyperion.editions.add(hyperion_print)
        hyperion.editions.add(hyperion_ebook)
        # andymion = Edition(title="Andymion", pages=42)
        # serie = Serie(title="Hyperion Cantos")


class GoodreadsTestCase(TestCase):
    def setUp(self):
        pass

    def test_parse(self):
        t_type = IdType.Goodreads
        t_id = "77566"
        t_url = "https://www.goodreads.com/zh/book/show/77566.Hyperion"
        t_url2 = "https://www.goodreads.com/book/show/77566"
        p1 = SiteManager.get_site_by_id_type(t_type)
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
        self.assertEqual(edition.title, "Hyperion")

        edition.delete()
        site = SiteManager.get_site_by_url(t_url)
        self.assertEqual(site.ready, False)
        self.assertEqual(site.url, t_url2)
        site.get_resource()
        self.assertEqual(
            site.ready, True, "previous resource should still exist with data"
        )

    @use_local_response
    def test_asin(self):
        t_url = "https://www.goodreads.com/book/show/45064996-hyperion"
        site = SiteManager.get_site_by_url(t_url)
        site.get_resource_ready()
        self.assertEqual(site.resource.item.title, "Hyperion")
        self.assertEqual(site.resource.item.asin, "B004G60EHS")

    @use_local_response
    def test_work(self):
        url = "https://www.goodreads.com/work/editions/153313"
        p = SiteManager.get_site_by_url(url).get_resource_ready()
        self.assertEqual(p.item.title, "1984")
        url1 = "https://www.goodreads.com/book/show/3597767-rok-1984"
        url2 = "https://www.goodreads.com/book/show/40961427-1984"
        p1 = SiteManager.get_site_by_url(url1).get_resource_ready()
        p2 = SiteManager.get_site_by_url(url2).get_resource_ready()
        w1 = p1.item.works.all().first()
        w2 = p2.item.works.all().first()
        self.assertEqual(w1, w2)


class GoogleBooksTestCase(TestCase):
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
        self.assertEqual(site.resource.item.title, "1984 Nineteen Eighty-Four")


class DoubanBookTestCase(TestCase):
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
        self.assertEqual(site.resource.item.title, "1984 Nineteen Eighty-Four")

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
        self.assertEqual(w1.title, "黄金时代")
        self.assertEqual(w2.title, "黄金时代")
        self.assertEqual(w1, w2)
        editions = w1.editions.all().order_by("title")
        self.assertEqual(editions.count(), 2)
        self.assertEqual(editions[0].title, "Wang in Love and Bondage")
        self.assertEqual(editions[1].title, "黄金时代")


class MultiBookSitesTestCase(TestCase):
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
        w2e = w2.editions.all().order_by("title")
        self.assertEqual(w2e.count(), 2)
        self.assertEqual(w2e[0].title, "Wang in Love and Bondage")
        self.assertEqual(w2e[1].title, "黄金时代")
        w3e = w3.editions.all().order_by("title")
        self.assertEqual(w3e.count(), 2)
        self.assertEqual(w3e[0].title, "Golden Age: A Novel")
        self.assertEqual(w3e[1].title, "黄金时代")
        e = Edition.objects.get(primary_lookup_id_value=9781662601217)
        self.assertEqual(e.title, "Golden Age: A Novel")
