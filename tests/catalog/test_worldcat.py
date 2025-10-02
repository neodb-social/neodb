import pytest

from catalog.common import SiteManager, use_local_response
from catalog.models import Edition, IdType, SiteName


@pytest.mark.django_db(databases="__all__")
class TestWorldCat:
    def test_parse(self):
        """Test URL parsing and ID extraction"""
        t_type = IdType.OCLC
        t_id = "687665134"
        t_url = "https://search.worldcat.org/title/687665134"
        p1 = SiteManager.get_site_cls_by_id_type(t_type)
        p2 = SiteManager.get_site_by_url(t_url)
        assert p1 is not None
        assert p1.id_to_url(t_id) == t_url
        assert p2 is not None
        assert p2.url_to_id(t_url) == t_id
        assert p2.ID_TYPE == t_type
        assert p2.id_value == t_id

    def test_url_patterns(self):
        """Test that various WorldCat URL patterns are recognized"""
        # Test search.worldcat.org URLs
        site1 = SiteManager.get_site_by_url(
            "https://search.worldcat.org/title/687665134"
        )
        assert site1 is not None
        assert site1.id_value == "687665134"

        # Test www.worldcat.org title URLs
        site2 = SiteManager.get_site_by_url("https://www.worldcat.org/title/687665134")
        assert site2 is not None
        assert site2.id_value == "687665134"

        # Test www.worldcat.org oclc URLs
        site3 = SiteManager.get_site_by_url("https://www.worldcat.org/oclc/687665134")
        assert site3 is not None
        assert site3.id_value == "687665134"

    @use_local_response
    def test_scrape_book(self):
        """Test scraping a book from WorldCat"""
        t_url = "https://search.worldcat.org/title/687665134"
        site = SiteManager.get_site_by_url(t_url)
        assert site is not None
        assert site.ready is False
        site.get_resource_ready()
        assert site.ready is True
        assert site.resource is not None
        assert site.resource.site_name == SiteName.WorldCat
        assert site.resource.id_type == IdType.OCLC
        assert site.resource.id_value == "687665134"

        metadata = site.resource.metadata
        assert "title" in metadata
        assert len(metadata["title"]) > 0
        assert metadata["title"] == "Tigerlily's orchids : a novel"
        assert site.resource.item is not None
        assert isinstance(site.resource.item, Edition)

        # Verify author
        assert "author" in metadata
        assert metadata["author"] == ["Ruth Rendell"]

        # Verify ISBN lookup
        assert site.resource.other_lookup_ids.get(IdType.ISBN) == "9781439150344"

        # Verify localized title
        assert metadata["localized_title"][0]["text"] == "Tigerlily's orchids : a novel"
        assert metadata["localized_title"][0]["lang"] == "en"

        # Verify publication year
        assert metadata["pub_year"] == 2011

        # Verify language
        assert "language" in metadata
        assert isinstance(metadata["language"], list)
