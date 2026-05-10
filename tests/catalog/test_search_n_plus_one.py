"""Regression tests for N+1 queries in catalog search."""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from catalog.models import TVSeason, TVShow
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

    def _patched_query_index(self, items_in_index):
        """Run ``query_index`` while the search index is mocked to return
        ``items_in_index``. Returns ``(items, captured_queries)``.
        """
        # Build a CatalogSearchResult from a synthetic response so the real
        # ``CatalogSearchResult.items`` cached_property runs (which is what
        # exercises Item.get_by_ids polymorphic load).
        response = {
            "hits": [{"document": {"id": str(it.pk)}} for it in items_in_index],
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
