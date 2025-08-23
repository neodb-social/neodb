import pytest

from catalog.book.utils import detect_isbn_asin
from catalog.common import SiteManager, use_local_response
from catalog.models import Edition, ExternalResource, IdType, Work


@pytest.mark.django_db(databases="__all__")
class TestArrayField:
    def test_legacy_data(self):
        o = Edition()
        assert o.other_title == []
        o.other_title = "test"
        assert o.other_title == ["test"]
        o.other_title = ["a", "b"]
        assert o.other_title == ["a", "b"]
        o.other_title = None
        assert o.other_title == []


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
