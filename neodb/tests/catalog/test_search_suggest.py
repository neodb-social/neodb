"""Search suggestions render straight from index hits, without loading items."""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from catalog.models import Edition, Movie, People, PeopleType
from catalog.search import PeopleIndex, suggest_items, suggest_people
from catalog.search.index import CatalogIndex, CatalogSearchResult
from catalog.search.people_index import PeopleSearchResult
from catalog.search.suggest import (
    SUGGEST_LIMIT,
    CatalogSuggestParser,
    PeopleSuggestParser,
    _cover_url,
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
        for f in ("uuid", "display_title", "cover", "item_class"):
            assert f in params["include_fields"]

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
        for f in ("uuid", "display_name", "cover", "people_type"):
            assert f in params["include_fields"]


@pytest.mark.django_db(databases="__all__")
class TestSuggestItems:
    def test_rows_from_index_only(self):
        docs = [
            {
                "id": "1",
                "item_class": "Edition",
                "uuid": "abc",
                "display_title": "The Three-Body Problem",
                "title": ["The Three-Body Problem", "三体"],
                "cover": "",
            },
            {
                "id": "2",
                "item_class": "TVSeason",
                "uuid": "def",
                "display_title": "Three Colors",
                "title": ["Three Colors"],
                "cover": "item/x.jpg",
            },
        ]
        patcher, index = _patch_catalog(docs)
        with patcher, CaptureQueriesContext(connection) as ctx:
            rows = suggest_items("thr")
        assert len(ctx.captured_queries) == 0
        assert [r.url for r in rows] == ["/book/abc", "/tv/season/def"]
        assert rows[0].title == "The Three-Body Problem"
        assert rows[0].category == "Book"
        assert rows[0].cover_url is None
        assert rows[0].matched == ""
        assert rows[1].category == "TV"
        assert rows[1].cover_url and rows[1].cover_url.endswith("item/x.jpg")
        index.search.assert_called_once()

    def test_matched_alt_title(self):
        docs = [
            {
                "id": "1",
                "item_class": "Edition",
                "uuid": "abc",
                "display_title": "The Three-Body Problem",
                "title": ["The Three-Body Problem", "三体"],
            }
        ]
        patcher, __ = _patch_catalog(docs)
        with patcher:
            rows = suggest_items("三")
        assert rows[0].matched == "三体"

    def test_stale_doc_falls_back_to_db(self):
        book = Edition.objects.create(
            localized_title=[{"lang": "en", "text": "Old Book"}]
        )
        movie = Movie.objects.create(
            localized_title=[{"lang": "en", "text": "Old Movie"}]
        )
        docs = [
            {"id": str(movie.pk), "item_class": "Movie", "title": ["Old Movie"]},
            {
                "id": "999999",
                "item_class": "Movie",
                "uuid": "gone",
                "display_title": "Fresh",
                "title": ["Fresh"],
            },
            {"id": str(book.pk), "item_class": "Edition", "title": ["Old Book"]},
        ]
        patcher, __ = _patch_catalog(docs)
        with patcher, CaptureQueriesContext(connection) as ctx:
            rows = suggest_items("old")
        # one polymorphic lookup for both stale hits (base row, one query per
        # concrete class, content type), hit order preserved
        assert len(ctx.captured_queries) <= 4
        assert [r.url for r in rows] == [movie.url, "/movie/gone", book.url]
        assert rows[0].title == "Old Movie"
        assert rows[0].category == "Movie"
        assert rows[2].category == "Book"

    def test_stale_doc_missing_in_db_is_dropped(self):
        docs = [{"id": "999999", "item_class": "Movie", "title": ["x"]}]
        patcher, __ = _patch_catalog(docs)
        with patcher:
            assert suggest_items("old") == []

    def test_unknown_class_falls_back_to_db(self):
        book = Edition.objects.create(localized_title=[{"lang": "en", "text": "B"}])
        docs = [{"id": str(book.pk), "item_class": "Nope", "uuid": "x"}]
        patcher, __ = _patch_catalog(docs)
        with patcher:
            rows = suggest_items("bb")
        assert [r.url for r in rows] == [book.url]

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
    def test_rows_from_index_only(self):
        docs = [
            {
                "id": "1",
                "people_type": "person",
                "uuid": "p1",
                "display_name": "Liu Cixin",
                "name": ["Liu Cixin", "刘慈欣"],
            },
            {
                "id": "2",
                "people_type": "organization",
                "uuid": "o1",
                "display_name": "Tor Books",
                "name": ["Tor Books"],
            },
        ]
        patcher, __ = _patch_people(docs)
        with patcher, CaptureQueriesContext(connection) as ctx:
            rows = suggest_people("刘")
        assert len(ctx.captured_queries) == 0
        assert [r.url for r in rows] == ["/person/p1", "/organization/o1"]
        assert rows[0].category == "Person"
        assert rows[0].matched == "刘慈欣"
        assert rows[1].category == "Organization"

    def test_stale_doc_falls_back_to_db(self):
        org = People.objects.create(
            localized_name=[{"lang": "en", "text": "Tor"}],
            people_type=PeopleType.ORGANIZATION,
        )
        docs = [{"id": str(org.pk), "people_type": "organization", "name": ["Tor"]}]
        patcher, __ = _patch_people(docs)
        with patcher:
            rows = suggest_people("tor")
        assert rows[0].url == org.url
        assert rows[0].url.startswith("/organization/")
        assert rows[0].title == "Tor"
        assert rows[0].category == "Organization"


@pytest.mark.django_db(databases="__all__")
class TestIndexedDocs:
    def test_item_doc_has_suggestion_fields(self):
        book = Edition.objects.create(
            localized_title=[
                {"lang": "zh-cn", "text": "三体"},
                {"lang": "en", "text": "The Three-Body Problem"},
            ]
        )
        doc = book.to_indexable_doc()
        assert doc["uuid"] == book.uuid
        assert doc["display_title"] in ("三体", "The Three-Body Problem")
        assert doc["cover"] == ""

    def test_season_doc_keeps_show_title(self):
        from catalog.models import TVSeason, TVShow

        show = TVShow.objects.create(
            localized_title=[{"lang": "en", "text": "Example Show"}]
        )
        season = TVSeason.objects.create(
            localized_title=[{"lang": "en", "text": "Season 2"}],
            show=show,
            season_number=2,
        )
        assert "Example Show" in season.to_indexable_doc()["display_title"]

    def test_default_display_title_ignores_request_language(self):
        from django.utils import translation

        with translation.override("zh-hans"):
            # saving indexes the item, which resolves the default-language
            # title; that must not poison the request-language cache
            book = Edition.objects.create(
                localized_title=[
                    {"lang": "en", "text": "English Title"},
                    {"lang": "zh-cn", "text": "中文标题"},
                ]
            )
            assert book.display_title == "中文标题"
            stored = book.default_display_title()
            # the request-language cache is untouched
            assert book.display_title == "中文标题"
        with translation.override("en"):
            expected = Edition.objects.get(pk=book.pk).display_title
        assert stored == expected

    def test_people_doc_has_suggestion_fields(self):
        person = People.objects.create(
            localized_name=[{"lang": "en", "text": "Liu Cixin"}],
            people_type=PeopleType.PERSON,
        )
        doc = PeopleIndex.person_to_doc(person)
        assert doc["uuid"] == person.uuid
        assert doc["display_name"] == "Liu Cixin"
        assert doc["cover"] == ""

    def test_schema_marks_fields_unindexed(self):
        for schema in (CatalogIndex.schema, PeopleIndex.schema):
            by_name = {f["name"]: f for f in schema["fields"]}
            for name in ("uuid", "cover"):
                assert by_name[name]["index"] is False
                assert by_name[name]["optional"] is True


def test_cover_url():
    assert _cover_url("") is None
    assert _cover_url(None) is None
    url = _cover_url("item/x.jpg")
    assert url and url.startswith("http") and url.endswith("item/x.jpg")


@pytest.mark.django_db(databases="__all__")
class TestSearchSuggestView:
    ROW = [
        {
            "id": "1",
            "item_class": "Edition",
            "uuid": "abc",
            "display_title": "The Three-Body Problem",
            "title": ["The Three-Body Problem", "三体"],
        }
    ]

    def test_renders_rows(self):
        patcher, __ = _patch_catalog(self.ROW)
        with patcher:
            resp = Client().get("/search/suggest?q=thr")
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'href="/book/abc"' in body
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
        patcher, index = _patch_catalog(self.ROW)
        with patcher:
            resp = Client().get(f"/search/suggest?{query}")
        assert resp.status_code == 200
        assert resp.content == b""
        index.search.assert_not_called()

    def test_people_category_uses_people_index(self):
        cpatch, cindex = _patch_catalog(self.ROW)
        ppatch, pindex = _patch_people(
            [
                {
                    "id": "1",
                    "people_type": "person",
                    "uuid": "p1",
                    "display_name": "Liu Cixin",
                    "name": ["Liu Cixin"],
                }
            ]
        )
        with cpatch, ppatch:
            resp = Client().get("/search/suggest?q=liu&c=people")
        assert 'href="/person/p1"' in resp.content.decode()
        cindex.search.assert_not_called()
        pindex.search.assert_called_once()

    def test_single_category_filters(self):
        patcher, index = _patch_catalog(self.ROW)
        with patcher:
            Client().get("/search/suggest?q=thr&c=book")
        q = index.search.call_args.args[0]
        assert "Edition" in q.to_search_params()["filter_by"]
