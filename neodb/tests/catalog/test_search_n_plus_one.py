"""Regression tests for N+1 queries in catalog search."""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalog.models import Edition, ExternalResource, IdType, Item, TVSeason, TVShow
from catalog.search.index import CatalogIndex, CatalogSearchResult
from catalog.search.utils import query_index


@pytest.mark.django_db(databases="__all__")
class TestSearchTVShowDedupNoNPlusOne:
    """EGGPLANT-188: ``query_index`` used to access ``season.show`` while
    deduping a show against its seasons in the result list, firing one
    ``catalog_tvshow`` lookup per TVSeason. Match by ``show_id`` instead so
    the count stays flat as the number of seasons grows.
    """

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.show = TVShow.objects.create(
            localized_title=[{"lang": "en", "text": "Sample Show"}]
        )
        self.seasons = [
            TVSeason.objects.create(
                localized_title=[{"lang": "en", "text": f"Sample Show Season {i}"}],
                show=self.show,
            )
            for i in range(1, 4)
        ]

    def _patched_query_index(self, items_in_index, tags_by_pk=None):
        """Run ``query_index`` while the search index is mocked to return
        ``items_in_index``. Returns ``(items, captured_queries)``.

        ``tags_by_pk`` optionally seeds the indexed ``tag`` field per item pk,
        mirroring what Typesense returns for ``include_fields``.
        """
        # Build a CatalogSearchResult from a synthetic response so the real
        # ``CatalogSearchResult.items`` cached_property runs (which is what
        # exercises Item.get_by_ids polymorphic load).
        tags_by_pk = tags_by_pk or {}
        response = {
            "hits": [
                {"document": {"id": str(it.pk), "tag": tags_by_pk.get(it.pk, [])}}
                for it in items_in_index
            ],
            "found": len(items_in_index),
            "page": 1,
            "request_params": {"per_page": 20, "q": "Sample"},
        }
        with patch.object(CatalogIndex, "instance") as mock_instance:
            mock_index = MagicMock(spec=CatalogIndex)
            mock_instance.return_value = mock_index
            result = CatalogSearchResult(mock_index, cast(Any, response))
            mock_index.search.return_value = result
            with CaptureQueriesContext(connection) as ctx:
                items, _pages, _total, _facets, _q = query_index(
                    "Sample", page=1, prepare_external=False
                )
        return items, ctx.captured_queries

    def test_show_deduped_into_season(self):
        items, _ = self._patched_query_index([self.seasons[0], self.show])
        # ``Item.get_by_ids`` returns fresh instances, so compare by pk.
        assert [i.pk for i in items] == [self.seasons[0].pk]
        assert [d.pk for d in getattr(items[0], "dupe_to", [])] == [self.show.pk]

    def test_no_per_season_tvshow_fk_lookup(self):
        """With multiple seasons in the result and a show present, the dedup
        path must not fire a per-season ``WHERE catalog_tvshow.item_ptr_id = X``
        query.
        """
        items, queries = self._patched_query_index(self.seasons + [self.show])

        # Sanity: the show was deduped, and exactly one season carries it.
        assert self.show.pk not in [i.pk for i in items]
        attached = [
            i
            for i in items
            if i.__class__ == TVSeason
            and [d.pk for d in getattr(i, "dupe_to", [])] == [self.show.pk]
        ]
        assert len(attached) == 1

        # The N+1 signature is Django's ``.get()`` on a single FK:
        # ``... FROM "catalog_tvshow" ... WHERE "catalog_tvshow"."item_ptr_id"
        # = %s LIMIT 21``. The JOIN clause also contains ``"item_ptr_id" =``,
        # so look for ``LIMIT 21`` to distinguish from polymorphic batch loads
        # (which use ``IN (...)`` and no LIMIT).
        offending = [
            q
            for q in queries
            if 'FROM "catalog_tvshow"' in q["sql"] and "LIMIT 21" in q["sql"]
        ]
        assert offending == [], (
            f"query_index fired {len(offending)} per-season catalog_tvshow "
            f"FK lookup(s); expected 0. First offending SQL: "
            f"{offending[0]['sql'] if offending else 'n/a'}"
        )

    def test_dupe_to_items_carry_indexed_tags_without_tagmember_query(self):
        """NEODB-SOCIAL-7KW: dropping ``Tag.attach_to_items`` from the search
        view must not reintroduce a per-``dupe_to`` tag query. ``dupe_to`` items
        are members of ``CatalogSearchResult.items`` (the deduped result reuses
        those same instances), so they already carry the indexed ``tag`` list.
        """
        tags = {self.seasons[0].pk: ["sci-fi"], self.show.pk: ["drama"]}
        items, queries = self._patched_query_index(
            [self.seasons[0], self.show], tags_by_pk=tags
        )

        # Primary item (season) and the show deduped onto its dupe_to both
        # carry their indexed tags.
        assert items[0].pk == self.seasons[0].pk
        assert items[0].tags == ["sci-fi"]
        dupes = getattr(items[0], "dupe_to", [])
        assert [d.pk for d in dupes] == [self.show.pk]
        # Reading the dupe's tags (as the template would) must not hit the DB.
        with CaptureQueriesContext(connection) as ctx:
            dupe_tags = dupes[0].tags
        assert dupe_tags == ["drama"]
        assert ctx.captured_queries == []

        # And nothing in the whole query_index path aggregated journal_tagmember.
        offending = [q for q in queries if "journal_tagmember" in q["sql"]]
        assert offending == [], (
            "query_index fired a journal_tagmember aggregation; dupe_to tags "
            f"should come from the index. First: {offending[0]['sql'] if offending else 'n/a'}"
        )


@pytest.mark.django_db(databases="__all__")
class TestSearchReusesIndexedTags:
    """NEODB-SOCIAL-7KW: ``CatalogSearchResult.items`` attaches the public tags
    stored in the search index onto each item, so search no longer re-aggregates
    ``journal_tagmember`` (a slow query for heavily-tagged items) per request.
    """

    def _result(self, document):
        response = {
            "hits": [{"document": document}],
            "found": 1,
            "page": 1,
            "request_params": {"per_page": 20, "q": "x"},
        }
        mock_index = MagicMock(spec=CatalogIndex)
        return CatalogSearchResult(mock_index, cast(Any, response))

    def test_indexed_tags_attached_without_tagmember_query(self):
        book = Edition.objects.create(title="Indexed Tags Book")
        result = self._result({"id": str(book.pk), "tag": ["fiction", "scifi"]})
        with CaptureQueriesContext(connection) as ctx:
            items = result.items
            tags = items[0].tags
        assert [i.pk for i in items] == [book.pk]
        assert tags == ["fiction", "scifi"]
        offending = [q for q in ctx.captured_queries if "journal_tagmember" in q["sql"]]
        assert offending == [], (
            "search hydration fired a journal_tagmember aggregation; tags should "
            f"come from the index. First offending SQL: {offending[0]['sql'] if offending else 'n/a'}"
        )

    def test_missing_tag_field_defaults_to_empty(self):
        book = Edition.objects.create(title="No Tags Book")
        result = self._result({"id": str(book.pk)})
        with CaptureQueriesContext(connection) as ctx:
            items = result.items
            tags = items[0].tags
        assert tags == []
        offending = [q for q in ctx.captured_queries if "journal_tagmember" in q["sql"]]
        assert offending == []


@pytest.mark.django_db(databases="__all__")
class TestSearchExternalResourcesSlim:
    """EGGPLANT-1DX: search loaded the full ``catalog_externalresource`` row
    (including the large ``metadata``/``other_lookup_ids`` JSON) for every
    result. Cards only read url/site_name/site_label, so the prefetch must skip
    those heavy columns.
    """

    def _run(self, items_in_index):
        response = {
            "hits": [{"document": {"id": str(it.pk)}} for it in items_in_index],
            "found": len(items_in_index),
            "page": 1,
            "request_params": {"per_page": 20, "q": "book"},
        }
        with patch.object(CatalogIndex, "instance") as mock_instance:
            mock_index = MagicMock(spec=CatalogIndex)
            mock_instance.return_value = mock_index
            result = CatalogSearchResult(mock_index, cast(Any, response))
            mock_index.search.return_value = result
            with CaptureQueriesContext(connection) as ctx:
                query_index("book", page=1, prepare_external=False)
        return ctx.captured_queries

    def test_get_by_ids_empty_fires_no_query(self):
        # get_by_ids short-circuits on an empty id list instead of building an
        # empty .extra() query.
        with CaptureQueriesContext(connection) as ctx:
            assert list(Item.get_by_ids([])) == []
        assert ctx.captured_queries == []

    def test_external_resources_prefetch_skips_heavy_json(self):
        book = Edition.objects.create(title="ExtRes Book")
        ExternalResource.objects.create(
            item=book,
            id_type=IdType.RSS,
            id_value="extres-1",
            url="https://example.com/extres-1",
            metadata={"big": "x" * 1000},
            other_lookup_ids={"isbn": "123"},
        )
        extres = [
            q
            for q in self._run([book])
            if 'FROM "catalog_externalresource"' in q["sql"]
        ]
        assert extres, "expected an external_resources prefetch query"
        for q in extres:
            assert '"metadata"' not in q["sql"], (
                f"search external_resources prefetch still selects metadata: {q['sql']}"
            )
            assert '"other_lookup_ids"' not in q["sql"], (
                "search external_resources prefetch still selects "
                f"other_lookup_ids: {q['sql']}"
            )
