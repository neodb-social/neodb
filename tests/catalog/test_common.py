import pytest

from catalog.common.downloaders import use_local_response
from catalog.common.sites import SiteManager
from catalog.models import Edition


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
class TestCatalogItem:
    @use_local_response
    def test_merge(self):
        url1 = "https://book.douban.com/subject/1089243/"
        site1 = SiteManager.get_site_by_url(url1)
        assert site1 is not None
        site1.get_resource_ready()
        assert site1.resource is not None
        edition1 = site1.resource.item
        assert isinstance(edition1, Edition)
        assert not edition1.has_cover()
        # no cover for this book as we excluded the file in test_data/
        site1.resource.metadata["language"] = ["cn"]
        edition1.language = ["cn"]  # type: ignore
        edition1.pages = None
        site1.resource.save()
        edition1.save()

        url2 = "https://www.goodreads.com/book/show/13079982-fahrenheit-451"
        site2 = SiteManager.get_site_by_url(url2)
        assert site2 is not None
        site2.get_resource_ready()
        assert site2.resource is not None
        edition2 = site2.resource.item
        assert isinstance(edition2, Edition)
        assert edition2.has_cover()
        assert edition2.language == ["en"]
        assert edition2.pages == 194

        edition2.merge_to(edition1)
        assert edition1.pages == 194
        assert sorted(edition1.language) == ["cn"]  # type: ignore
        assert edition1.has_cover()
