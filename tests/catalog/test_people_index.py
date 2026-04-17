import pytest

from catalog.models import Edition, Movie
from catalog.models.people import People, PeopleType
from catalog.search import CatalogIndex
from catalog.search.people_index import PeopleIndex, PeopleQueryParser


@pytest.mark.django_db(databases="__all__")
class TestPeopleQueryParser:
    def test_basic(self):
        parser = PeopleQueryParser("tolkien", 1, 20)
        assert parser.q == "tolkien"
        assert parser.filter_by == {}

    def test_people_type_filter(self):
        parser = PeopleQueryParser("acme type:organization", 1, 20)
        assert parser.q == "acme"
        assert parser.filter_by.get("people_type") == ["organization"]

    def test_people_type_kwarg(self):
        parser = PeopleQueryParser("tolkien", 1, 20, people_type="person")
        assert parser.filter_by.get("people_type") == ["person"]

    def test_people_type_kwarg_overridden_by_field(self):
        parser = PeopleQueryParser(
            "acme type:organization", 1, 20, people_type="person"
        )
        assert parser.filter_by.get("people_type") == ["organization"]

    def test_id_filter(self):
        parser = PeopleQueryParser("id:nm0000001", 1, 20)
        assert parser.q == ""
        assert "lookup_id:=`nm0000001`" in parser.filter_by.get("_", [])


@pytest.mark.django_db(databases="__all__")
class TestPeopleIndex:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        CatalogIndex().delete_all()
        PeopleIndex().delete_all()

        self.tolkien = People.objects.create(
            title="J.R.R. Tolkien", people_type=PeopleType.PERSON
        )
        self.tolkien.localized_name = [
            {"lang": "en", "text": "J.R.R. Tolkien"},
        ]
        self.tolkien.save()

        self.coppola = People.objects.create(
            title="Francis Ford Coppola", people_type=PeopleType.PERSON
        )
        self.coppola.localized_name = [
            {"lang": "en", "text": "Francis Ford Coppola"},
        ]
        self.coppola.save()

        self.allen_unwin = People.objects.create(
            title="Allen & Unwin", people_type=PeopleType.ORGANIZATION
        )
        self.allen_unwin.localized_name = [
            {"lang": "en", "text": "Allen & Unwin"},
        ]
        self.allen_unwin.save()

        for person in (self.tolkien, self.coppola, self.allen_unwin):
            PeopleIndex.instance().replace_person(person)

        yield

        for person in (self.tolkien, self.coppola, self.allen_unwin):
            PeopleIndex.instance().delete_person(person)

    def _ids(self, result):
        return [p.pk for p in result.items]

    def test_search_by_name(self):
        result = PeopleIndex.instance().search(PeopleQueryParser("Tolkien", 1, 20))
        assert self.tolkien.pk in self._ids(result)
        assert self.coppola.pk not in self._ids(result)

    def test_filter_by_type(self):
        result = PeopleIndex.instance().search(
            PeopleQueryParser("", 1, 20, people_type="organization")
        )
        assert self.allen_unwin.pk in self._ids(result)
        assert self.tolkien.pk not in self._ids(result)
        assert self.coppola.pk not in self._ids(result)

    def test_deleted_person_removed(self):
        self.coppola.is_deleted = True
        self.coppola.save()
        PeopleIndex.instance().replace_person(self.coppola)
        result = PeopleIndex.instance().search(PeopleQueryParser("Coppola", 1, 20))
        assert self.coppola.pk not in self._ids(result)

    def test_find_by_name_uses_index(self):
        results = People.find_by_name("Tolkien", exact=False, limit=10)
        assert self.tolkien in results
        assert self.coppola not in results

    def test_find_by_name_exact_uses_db(self):
        results = People.find_by_name("J.R.R. Tolkien", exact=True)
        assert self.tolkien in results

    def test_person_to_doc_skips_empty_name(self):
        empty = People.objects.create(title="x", people_type=PeopleType.PERSON)
        empty.localized_name = []
        empty.save()
        doc = PeopleIndex.person_to_doc(empty)
        assert doc == {}

    def test_person_to_doc_prefers_annotated_credit_count(self):
        # Simulate a bulk-loaded instance with .annotate(credit_count=Count(...))
        setattr(self.tolkien, "credit_count", 42)
        doc = PeopleIndex.person_to_doc(self.tolkien)
        assert doc["credit_count"] == 42

    def test_find_by_name_raises_on_index_error(self):
        from unittest.mock import MagicMock, patch

        err = MagicMock()
        err.error = "typesense down"
        err.items = []
        with patch.object(PeopleIndex, "search", return_value=err):
            with pytest.raises(RuntimeError):
                People.find_by_name("anything", exact=False)

    def test_people_search_view_flags_error_on_outage(self, client):
        """Regression: SearchResult is falsy when there are no hits, so the
        view must detect the error via .error (not via bool(result))."""
        from unittest.mock import MagicMock, patch

        outage = MagicMock()
        outage.error = "typesense down"
        outage.__bool__.return_value = False  # no hits
        outage.items = []
        outage.pages = 0
        with patch.object(PeopleIndex, "search", return_value=outage):
            resp = client.get("/search?q=tolkien&c=people")
        assert resp.status_code == 200
        assert resp.context["search_error"] is True
        assert resp.context["items"] == []


@pytest.mark.django_db(databases="__all__")
class TestCatalogExcludesPeople:
    @pytest.fixture(autouse=True)
    def setup_data(self):
        CatalogIndex().delete_all()
        PeopleIndex().delete_all()

        self.book = Edition.objects.create(title="Test Book")
        self.book.isbn = "9781234567891"
        self.book.save()

        self.movie = Movie.objects.create(title="Test Movie")
        self.movie.save()

        self.person = People.objects.create(
            title="Some Person", people_type=PeopleType.PERSON
        )
        self.person.localized_name = [{"lang": "en", "text": "Some Person"}]
        self.person.save()

        yield

    def test_people_not_indexed_in_catalog_after_save(self):
        # People.save() -> update_index() routes to PeopleIndex, not CatalogIndex.
        # Verify directly from the catalog index.
        from typesense.exceptions import ObjectNotFound

        with pytest.raises(ObjectNotFound):
            CatalogIndex.instance().get_doc(self.person.pk)

    def test_people_indexed_in_people_collection(self):
        doc = PeopleIndex.instance().get_doc(self.person.pk)
        assert doc["people_type"] == "person"
        assert "Some Person" in doc["name"]
