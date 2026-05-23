import pytest

from catalog.common.downloaders import use_local_response
from catalog.common.sites import SiteManager
from catalog.models import Edition, Movie


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

    def test_tolerant_array_form_field_empty_string(self):
        """Regression for EGGPLANT-1CM: empty/blank string POSTed for an
        ArrayField widget must not raise JSONDecodeError."""
        from common.models.jsondata import ArrayField, TolerantArrayFormField

        field = Movie._meta.get_field("genre")
        assert isinstance(field, ArrayField)
        f = field.formfield()
        assert isinstance(f, TolerantArrayFormField)
        assert f.to_python("") == []
        assert f.to_python("   ") == []
        assert f.to_python("not json") == []
        assert f.to_python("[]") == []
        assert f.to_python('["drama"]') == ["drama"]

    def test_edit_form_accepts_blank_array_fields(self):
        """Regression for EGGPLANT-1CM: posting an edit with blank array
        fields must not crash form validation."""
        from catalog.forms import CatalogForms

        movie = Movie.objects.create(title="Test Movie")
        form_cls = CatalogForms["Movie"]
        post_data = {
            "id": movie.pk,
            "localized_title": '[{"lang":"en","text":"Test Movie"}]',
            "localized_description": '[{"lang":"en","text":""}]',
            "genre": "",
            "language": "",
            "area": "",
            "director": "",
            "playwright": "",
            "actor": "",
            "producer": "",
            "primary_lookup_id_type": "",
            "primary_lookup_id_value": "",
        }
        form = form_cls(post_data, instance=movie)
        # Must not raise JSONDecodeError; validity itself is not the point.
        form.is_valid()
        assert form.cleaned_data.get("genre", None) in ([], None)


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
        edition1.language = ["cn"]
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
        assert sorted(edition1.language) == ["cn"]
        assert edition1.has_cover()
