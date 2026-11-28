from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pytest_httpx import HTTPXMock

from catalog.common import SiteManager, use_local_response
from catalog.common.downloaders import get_mock_file
from catalog.models import Edition, IdType, People, SiteName, Work
from catalog.sites.openlibrary import OpenLibrary, openlibrary_limiter


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
        related = metadata.get("related_resources") or []
        author_refs = [
            r for r in related if r.get("id_type") == IdType.OpenLibrary_Author
        ]
        assert author_refs
        assert author_refs[0]["model"] == "People"
        assert author_refs[0]["id_value"] == "OL34184A"
        assert author_refs[0]["url"] == "https://openlibrary.org/authors/OL34184A"

        (work,) = metadata["required_resources"]
        assert work["model"] == "Work"
        assert work["id_type"] == IdType.OpenLibrary_Work
        assert work["id_value"] == "OL45804W"
        assert work["url"] == "https://openlibrary.org/works/OL45804W"


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
        editions_only = [
            r
            for r in metadata["related_resources"]
            if r.get("id_type") == IdType.OpenLibrary
        ]
        assert editions_only
        first_edition = editions_only[0]
        assert first_edition["model"] == "Edition"
        assert first_edition["id_type"] == IdType.OpenLibrary
        assert "id_value" in first_edition
        assert first_edition["id_value"].endswith("M")  # OpenLibrary edition format
        assert "url" in first_edition
        assert first_edition["url"].startswith("https://openlibrary.org/books/")

        # Verify author People resource is present
        author_refs = [
            r
            for r in metadata["related_resources"]
            if r.get("id_type") == IdType.OpenLibrary_Author
        ]
        assert author_refs
        assert author_refs[0]["model"] == "People"
        assert author_refs[0]["id_value"] == "OL34184A"
        assert author_refs[0]["url"] == "https://openlibrary.org/authors/OL34184A"

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


@pytest.mark.django_db(databases="__all__")
class TestOpenLibraryAuthor:
    def test_parse(self):
        t_type = IdType.OpenLibrary_Author
        t_id = "OL34184A"
        t_url = "https://openlibrary.org/authors/OL34184A"
        p1 = SiteManager.get_site_cls_by_id_type(t_type)
        p2 = SiteManager.get_site_by_url(t_url)
        assert p1 is not None
        assert p1.id_to_url(t_id) == t_url
        assert p2 is not None
        assert p2.url_to_id(t_url) == t_id
        assert p2.ID_TYPE == t_type
        assert p2.id_value == t_id

    @use_local_response
    def test_scrape_author(self):
        t_url = "https://openlibrary.org/authors/OL34184A"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        site.get_resource_ready()
        assert site.resource is not None
        assert site.resource.site_name == SiteName.OpenLibrary
        assert site.resource.id_type == IdType.OpenLibrary_Author
        assert site.resource.id_value == "OL34184A"
        assert isinstance(site.resource.item, People)

        metadata = site.resource.metadata
        assert metadata["title"] == "Roald Dahl"
        assert metadata["birth_date"] == "1916-09-13"
        assert metadata["death_date"] == "1990-11-23"
        assert metadata["official_site"] == "http://www.roalddahl.com/"
        names = {n["text"] for n in metadata["localized_name"]}
        assert "Roald Dahl" in names
        assert (metadata.get("cover_image_url") or "").startswith(
            "https://covers.openlibrary.org/a/id/"
        )

        lookup = site.resource.other_lookup_ids
        assert lookup.get(IdType.WikiData) == "Q25161"
        assert lookup.get(IdType.Goodreads_Author) == "4273"
        assert lookup.get(IdType.IMDB) == "nm0001094"


@pytest.mark.asyncio
async def test_openlibrary_search_categories(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    acquire = AsyncMock(return_value=True)
    monkeypatch.setattr(openlibrary_limiter(), "try_acquire_async", acquire)
    fixture_url = (
        "https://openlibrary.org/search.json?title=Fantastic+Mr+Fox&author=Roald+Dahl"
        "&limit=3&fields=key,title,editions,editions.key,editions.title"
    )
    fixture = (
        Path(__file__).resolve().parents[2] / "test_data" / get_mock_file(fixture_url)
    )
    httpx_mock.add_response(content=fixture.read_bytes())

    assert await OpenLibrary.search_task("Fantastic Mr Fox", 1, "movie", 3) == []
    acquire.assert_not_awaited()
    (result,) = await OpenLibrary.search_task("Fantastic Mr Fox", 1, "book", 3)
    assert result.display_title == "Fantastic Mr. Fox"
    assert result.source_url == "https://openlibrary.org/books/OL7353617M"
