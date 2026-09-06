"""Search suggestions render from index hits plus one flat query for links."""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from catalog.models import Edition, Movie, People, PeopleType, TVSeason, TVShow
from catalog.search import PeopleIndex, suggest_items, suggest_people
from catalog.search.index import CatalogIndex, CatalogSearchResult
from catalog.search.people_index import PeopleSearchResult
from catalog.search.suggest import (
    SUGGEST_LIMIT,
    CatalogSuggestParser,
    PeopleSuggestParser,
    _cover_url,
    _titles,
)


def _response(docs: list[dict], q: str = "thr") -> Any:
    return {
        "hits": [{"document": d} for d in docs],
        "found": len(docs),
        "page": 1,
        "request_params": {"per_page": SUGGEST_LIMIT, "q": q},
    }


def _patch_catalog(docs: list[dict]):
    index = MagicMock(spec=CatalogIndex)
    index.search.return_value = CatalogSearchResult(index, _response(docs))
    return patch.object(CatalogIndex, "instance", return_value=index), index


def _patch_people(docs: list[dict]):
    index = MagicMock(spec=PeopleIndex)
    index.search.return_value = PeopleSearchResult(index, _response(docs))
    return patch.object(PeopleIndex, "instance", return_value=index), index


class TestSuggestParsers:
    def test_catalog_params(self):
        params = CatalogSuggestParser("thr", page_size=SUGGEST_LIMIT).to_search_params()
        assert params["q"] == "thr"
        assert params["per_page"] == SUGGEST_LIMIT
        assert params["num_typos"] == 1
        assert params["drop_tokens_threshold"] == 0
        assert params["exhaustive_search"] is False
        assert params["search_cutoff_ms"] == 50
        assert "facet_by" not in params
        assert f"bucket_size:{SUGGEST_LIMIT}" in params["sort_by"]
        assert params["include_fields"] == "id, item_class, title"

    def test_catalog_category_filter(self):
        from catalog.models import ItemCategory

        params = CatalogSuggestParser(
            "thr", page_size=SUGGEST_LIMIT, filter_categories=[ItemCategory.Book]
        ).to_search_params()
        assert "item_class:" in params["filter_by"]
        assert "Edition" in params["filter_by"]

    def test_people_params(self):
        params = PeopleSuggestParser("liu", page_size=SUGGEST_LIMIT).to_search_params()
        assert params["query_by"] == "name, lookup_id"
        assert "facet_by" not in params
        assert params["include_fields"] == "id, people_type, name"

    def test_suggest_fields_are_all_indexed(self):
        """The suggestion path must not need a field the schema lacks."""
        for schema, parser in (
            (CatalogIndex.schema, CatalogSuggestParser),
            (PeopleIndex.schema, PeopleSuggestParser),
        ):
            declared = {f["name"] for f in schema["fields"]} | {"id"}
            wanted = {
                f.strip()
                for f in parser.default_search_params["include_fields"].split(",")
            }
            assert wanted <= declared


class TestTitleChoice:
    def test_prefers_the_title_the_query_matched(self):
        titles = ["The Three-Body Problem", "三体"]
        assert _titles("三", titles) == ("三体", "The Three-Body Problem")
        assert _titles("thr", titles) == ("The Three-Body Problem", "三体")

    def test_falls_back_to_the_first_title(self):
        assert _titles("zzz", ["Only One"]) == ("Only One", "")

    def test_no_titles(self):
        assert _titles("q", []) == ("", "")
        assert _titles("q", ["", ""]) == ("", "")


@pytest.mark.django_db(databases="__all__")
class TestSuggestItems:
    def test_rows_need_one_query(self):
        book = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "The Three-Body Problem"}]
        )
        movie = Movie.objects.create(
            localized_title=[{"lang": "en", "text": "Three Colors: Blue"}]
        )
        docs = [
            {
                "id": str(book.pk),
                "item_class": "Edition",
                "title": ["The Three-Body Problem", "三体"],
            },
            {
                "id": str(movie.pk),
                "item_class": "Movie",
                "title": ["Three Colors: Blue"],
            },
        ]
        patcher, index = _patch_catalog(docs)
        with patcher, CaptureQueriesContext(connection) as ctx:
            rows = suggest_items("thr")
        # one flat query for the whole page of hits, no polymorphic descent
        assert len(ctx.captured_queries) == 1
        assert [r.url for r in rows] == [book.url, movie.url]
        assert rows[0].title == "The Three-Body Problem"
        assert rows[0].alt_title == "三体"
        assert rows[0].category == "Book"
        assert rows[0].cover_url is None
        assert rows[1].category == "Movie"
        index.search.assert_called_once()

    def test_season_links_to_the_season(self):
        show = TVShow.objects.create(
            localized_title=[{"lang": "en", "text": "Example Show"}]
        )
        season = TVSeason.objects.create(
            localized_title=[{"lang": "en", "text": "Season 2"}],
            show=show,
            season_number=2,
        )
        # a season indexes its show's titles too (Item.to_indexable_titles)
        docs = [
            {
                "id": str(season.pk),
                "item_class": "TVSeason",
                "title": season.to_indexable_titles(),
            }
        ]
        patcher, __ = _patch_catalog(docs)
        with patcher:
            rows = suggest_items("example")
        assert rows[0].url == season.url
        assert rows[0].category == "TV"
        assert "Example Show" in (rows[0].title, rows[0].alt_title)

    def test_gone_deleted_and_merged_rows_are_dropped(self):
        kept = Movie.objects.create(localized_title=[{"lang": "en", "text": "Kept"}])
        deleted = Movie.objects.create(
            localized_title=[{"lang": "en", "text": "Deleted"}], is_deleted=True
        )
        merged = Movie.objects.create(
            localized_title=[{"lang": "en", "text": "Merged"}], merged_to_item=kept
        )
        docs = [
            {"id": str(deleted.pk), "item_class": "Movie", "title": ["Deleted"]},
            {"id": str(kept.pk), "item_class": "Movie", "title": ["Kept"]},
            {"id": str(merged.pk), "item_class": "Movie", "title": ["Merged"]},
            {"id": "999999", "item_class": "Movie", "title": ["Gone"]},
        ]
        patcher, __ = _patch_catalog(docs)
        with patcher:
            rows = suggest_items("ept")
        assert [r.url for r in rows] == [kept.url]

    def test_unknown_class_is_dropped(self):
        book = Edition.objects.create(localized_title=[{"lang": "en", "text": "B"}])
        docs = [{"id": str(book.pk), "item_class": "Nope", "title": ["B"]}]
        patcher, __ = _patch_catalog(docs)
        with patcher:
            assert suggest_items("bb") == []

    def test_no_hits_skips_the_query(self):
        patcher, __ = _patch_catalog([])
        with patcher, CaptureQueriesContext(connection) as ctx:
            assert suggest_items("thr") == []
        assert len(ctx.captured_queries) == 0

    @pytest.mark.parametrize("q", ["", "a", "x" * 101])
    def test_short_or_long_query_skips_index(self, q):
        patcher, index = _patch_catalog([])
        with patcher:
            assert suggest_items(q) == []
        index.search.assert_not_called()

    def test_single_cjk_char_queries_index(self):
        patcher, index = _patch_catalog([])
        with patcher:
            assert suggest_items("三") == []
        index.search.assert_called_once()

    def test_index_error_gives_no_rows(self):
        index = MagicMock(spec=CatalogIndex)
        index.search.return_value = CatalogSearchResult(
            index, cast(Any, {"error": "boom", "code": -1})
        )
        with patch.object(CatalogIndex, "instance", return_value=index):
            assert suggest_items("thr") == []


@pytest.mark.django_db(databases="__all__")
class TestSuggestPeople:
    def test_person_and_organization_urls(self):
        person = People.objects.create(
            localized_name=[{"lang": "en", "text": "Liu Cixin"}],
            people_type=PeopleType.PERSON,
        )
        org = People.objects.create(
            localized_name=[{"lang": "en", "text": "Tor Books"}],
            people_type=PeopleType.ORGANIZATION,
        )
        docs = [
            {
                "id": str(person.pk),
                "people_type": "person",
                "name": ["Liu Cixin", "刘慈欣"],
            },
            {"id": str(org.pk), "people_type": "organization", "name": ["Tor Books"]},
        ]
        patcher, __ = _patch_people(docs)
        with patcher, CaptureQueriesContext(connection) as ctx:
            rows = suggest_people("liu")
        assert len(ctx.captured_queries) == 1
        assert [r.url for r in rows] == [person.url, org.url]
        assert rows[0].url.startswith("/person/")
        assert rows[0].category == "Person"
        assert rows[0].alt_title == "刘慈欣"
        assert rows[1].url.startswith("/organization/")
        assert rows[1].category == "Organization"

    def test_gone_row_is_dropped(self):
        docs = [{"id": "999999", "people_type": "person", "name": ["Gone"]}]
        patcher, __ = _patch_people(docs)
        with patcher:
            assert suggest_people("gone") == []


def test_cover_url():
    assert _cover_url("") is None
    url = _cover_url("item/x.jpg")
    assert url and url.startswith("http") and url.endswith("item/x.jpg")


@pytest.mark.django_db(databases="__all__")
class TestSearchSuggestView:
    @pytest.fixture(autouse=True)
    def an_item(self):
        self.book = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "The Three-Body Problem"}]
        )
        self.docs = [
            {
                "id": str(self.book.pk),
                "item_class": "Edition",
                "title": ["The Three-Body Problem"],
            }
        ]

    def test_renders_rows(self):
        patcher, __ = _patch_catalog(self.docs)
        with patcher:
            resp = Client().get("/search/suggest?q=thr")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert f'href="{self.book.url}"' in body
        assert "The Three-Body Problem" in body
        assert "Book" in body

    def test_no_rows_is_empty_body(self):
        patcher, __ = _patch_catalog([])
        with patcher:
            resp = Client().get("/search/suggest?q=zzz")
        assert resp.status_code == 200
        assert resp.content == b""

    @pytest.mark.parametrize(
        "query",
        [
            "q=%40someone",
            "q=https%3A%2F%2Fexample.org%2Fx",
            "q=thr&c=journal",
            "q=thr&c=timeline",
            "q=",
        ],
    )
    def test_short_circuits_skip_index(self, query):
        patcher, index = _patch_catalog(self.docs)
        with patcher:
            resp = Client().get(f"/search/suggest?{query}")
        assert resp.status_code == 200
        assert resp.content == b""
        index.search.assert_not_called()

    def test_people_category_uses_people_index(self):
        person = People.objects.create(
            localized_name=[{"lang": "en", "text": "Liu Cixin"}],
            people_type=PeopleType.PERSON,
        )
        cpatch, cindex = _patch_catalog(self.docs)
        ppatch, pindex = _patch_people(
            [{"id": str(person.pk), "people_type": "person", "name": ["Liu Cixin"]}]
        )
        with cpatch, ppatch:
            resp = Client().get("/search/suggest?q=liu&c=people")
        assert f'href="{person.url}"' in resp.content.decode()
        cindex.search.assert_not_called()
        pindex.search.assert_called_once()

    def test_single_category_filters(self):
        patcher, index = _patch_catalog(self.docs)
        with patcher:
            Client().get("/search/suggest?q=thr&c=book")
        q = index.search.call_args.args[0]
        assert "Edition" in q.to_search_params()["filter_by"]
