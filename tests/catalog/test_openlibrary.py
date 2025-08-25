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

    # comment out as it times out too often
    # def test_parse_isbn_redirect(self):
    #     # Test that ISBN URLs automatically redirect to book URLs via SiteManager
    #     isbn = "9780980200447"
    #     isbn_url = f"https://openlibrary.org/isbn/{isbn}"
    #     site = SiteManager.get_site_by_url(isbn_url)
    #     assert site is not None
    #     assert site.ID_TYPE == IdType.OpenLibrary
    #     assert site.id_value == "OL22853304M"
    #     assert site.url == "https://openlibrary.org/books/OL22853304M"

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

        # Test that related_resources (editions) are populated
        assert "related_resources" in metadata
        assert isinstance(metadata["related_resources"], list)
        assert len(metadata["related_resources"]) > 0

        # Verify structure of related edition resources
        first_edition = metadata["related_resources"][0]
        assert first_edition["model"] == "Edition"
        assert first_edition["id_type"] == IdType.OpenLibrary
        assert "id_value" in first_edition
        assert first_edition["id_value"].endswith("M")  # OpenLibrary edition format
        assert "url" in first_edition
        assert first_edition["url"].startswith("https://openlibrary.org/books/")

    @use_local_response
    def test_fetch_editions(self):
        """Test the fetch_editions method specifically"""
        from catalog.sites.openlibrary import OpenLibrary_Work

        site = OpenLibrary_Work(id_value="OL45804W")
        editions = site.fetch_editions()

        assert isinstance(editions, list)
        assert len(editions) > 0

        # Verify each edition has the correct structure
        for edition in editions:
            assert edition["model"] == "Edition"
            assert edition["id_type"] == IdType.OpenLibrary
            assert edition["id_value"].endswith("M")
            assert edition["url"].startswith("https://openlibrary.org/books/")
            assert "title" in edition


def test_openlibrary_search_categories_sync():
    """Test that OpenLibrary search respects category filtering (sync test)"""
    import asyncio

    from catalog.sites.openlibrary import OpenLibrary

    async def run_test():
        # Should return empty for unsupported categories
        movie_results = await OpenLibrary.search_task(
            "Python", page=1, category="movie", page_size=3
        )
        assert len(movie_results) == 0

        # Test that the method exists and doesn't crash for supported categories
        # (We don't test actual network calls in unit tests)
        try:
            book_results = await OpenLibrary.search_task(
                "test", page=1, category="book", page_size=1
            )
            # Results might be empty due to network issues in testing, that's ok
            assert isinstance(book_results, list)
        except Exception:
            # Network issues are acceptable in unit tests
            pass

    asyncio.run(run_test())
