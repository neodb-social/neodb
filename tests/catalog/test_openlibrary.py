import pytest

from catalog.common import SiteManager, use_local_response
from catalog.models import Edition, IdType, SiteName, Work


@pytest.mark.django_db(databases="__all__")
class TestOpenLibrary:
    def test_parse(self):
        t_type = IdType.OpenLibrary
        t_id = "OL7353617M"
        t_url = "https://openlibrary.org/books/OL7353617M"
        t_url2 = "https://openlibrary.org/books/OL7353617M"
        p1 = SiteManager.get_site_cls_by_id_type(t_type)
        p2 = SiteManager.get_site_by_url(t_url)
        assert p1 is not None
        assert p1.id_to_url(t_id) == t_url2
        assert p2 is not None
        assert p2.url_to_id(t_url) == t_id
        assert p2.ID_TYPE == t_type
        assert p2.id_value == t_id

    def test_parse_isbn_redirect(self):
        # Test that ISBN URLs automatically redirect to book URLs via SiteManager
        isbn = "9780980200447"
        isbn_url = f"https://openlibrary.org/isbn/{isbn}"
        site = SiteManager.get_site_by_url(isbn_url)
        assert site is not None
        assert site.ID_TYPE == IdType.OpenLibrary
        assert site.id_value == "OL22853304M"
        assert site.url == "https://openlibrary.org/books/OL22853304M"

    @use_local_response
    def test_scrape_book(self):
        t_url = "https://openlibrary.org/books/OL7353617M"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.ready is False
        site.get_resource_ready()
        assert site.ready is True
        assert site.resource is not None
        assert site.resource.site_name == SiteName.OpenLibrary
        assert site.resource.id_type == IdType.OpenLibrary
        assert site.resource.id_value == "OL7353617M"
        metadata = site.resource.metadata
        assert "title" in metadata
        assert len(metadata["title"]) > 0
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Edition)
        assert site.resource.other_lookup_ids.get(IdType.ISBN) == "9780140328721"
        assert metadata["localized_title"] == [
            {"lang": "en", "text": "Fantastic Mr. Fox"}
        ]
        assert metadata["author"] == ["Roald Dahl"]

    @use_local_response
    def test_work_relationship(self):
        t_url = "https://openlibrary.org/books/OL7353617M"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.resource is not None

        metadata = site.resource.metadata
        if "required_resources" in metadata and metadata["required_resources"]:
            work_resource = metadata["required_resources"][0]
            assert work_resource["model"] == "Work"
            assert work_resource["id_type"] == IdType.OpenLibrary_Work
            assert "id_value" in work_resource
            assert "url" in work_resource


@pytest.mark.django_db(databases="__all__")
class TestOpenLibraryWork:
    def test_parse(self):
        t_type = IdType.OpenLibrary_Work
        t_id = "OL45804W"
        t_url = "https://openlibrary.org/works/OL45804W"
        p1 = SiteManager.get_site_cls_by_id_type(t_type)
        p2 = SiteManager.get_site_by_url(t_url)
        assert p1 is not None
        assert p1.id_to_url(t_id) == t_url
        assert p2 is not None
        assert p2.url_to_id(t_url) == t_id
        assert p2.ID_TYPE == t_type
        assert p2.id_value == t_id

    @use_local_response
    def test_scrape_work(self):
        t_url = "https://openlibrary.org/works/OL45804W"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.ready is False
        site.get_resource_ready()
        assert site.ready is True
        assert site.resource is not None
        assert site.resource.site_name == SiteName.OpenLibrary
        assert site.resource.id_type == IdType.OpenLibrary_Work
        assert site.resource.id_value == "OL45804W"
        metadata = site.resource.metadata
        assert metadata["localized_title"] == [
            {"lang": "en", "text": "Fantastic Mr Fox"}
        ]
        assert isinstance(site.resource.item, Work)
        assert isinstance(metadata["localized_description"], list)
        assert len(metadata["localized_description"]) > 0
        desc = metadata["localized_description"][0]
        assert "lang" in desc
        assert "text" in desc
        assert len(desc["text"]) > 0
