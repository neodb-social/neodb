"""Regression tests for N+1 queries in catalog search."""

from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from catalog.models import (
    Edition,
    ExternalResource,
    IdType,
    Item,
    ItemCredit,
    Podcast,
    PodcastEpisode,
    TVEpisode,
    TVSeason,
    TVShow,
)
from catalog.search.index import CatalogIndex, CatalogSearchResult
from catalog.search.utils import query_index
from journal.models import Mark, ShelfType, TagManager
from users.models import User


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


@pytest.mark.django_db(databases="__all__")
class TestCatalogIndexBatchNoNPlusOne:
    """NEODB-SOCIAL-7W5: ``CatalogIndex.replace_items`` built every document on
    its own, so each item in the batch cost one query for its credits, one for
    its public tags and one for its mark count, plus one for the parent title
    of a TVSeason. Batch them, so the count stays flat as the batch grows.
    """

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.owner = User.register(email="idxn1a@test.com", username="idxn1a").identity
        self.other = User.register(email="idxn1b@test.com", username="idxn1b").identity

    def _make_editions(self, count: int, prefix: str) -> list[Edition]:
        editions = []
        for n in range(count):
            edition = Edition.objects.create(title=f"{prefix} {n}")
            ItemCredit.objects.create(item=edition, role="author", name=f"Author {n}")
            Mark(self.owner, edition).update(
                ShelfType.COMPLETE, visibility=0, tags=["sci-fi", "read"]
            )
            editions.append(edition)
        return editions

    @staticmethod
    def _replace_items(item_ids: list[int]):
        """Run ``replace_items`` against a mocked index, returning the queries.

        Only ``replace_docs``/``delete_docs`` reach Typesense, so a mock stands
        in for ``self`` and the database work under test runs unchanged.
        """
        index = MagicMock(spec=CatalogIndex)
        with CaptureQueriesContext(connection) as ctx:
            CatalogIndex.replace_items(index, item_ids)
        return index, ctx.captured_queries

    def test_query_count_does_not_grow_with_batch_size(self):
        # Same class mix in both batches: a polymorphic load costs one query
        # per concrete class, which would otherwise mask the difference.
        small = self._make_editions(2, "Small")
        large = self._make_editions(6, "Large")

        index_small, q_small = self._replace_items([i.pk for i in small])
        index_large, q_large = self._replace_items([i.pk for i in large])

        # Sanity: each batch produced one document per item.
        assert len(index_small.replace_docs.call_args[0][0]) == 2
        assert len(index_large.replace_docs.call_args[0][0]) == 6

        assert len(q_large) == len(q_small), (
            f"replace_items fired {len(q_large)} queries for 6 items but only "
            f"{len(q_small)} for 2, so it still scales with the batch. Extra "
            f"SQL: {[q['sql'] for q in q_large][len(q_small) :]}"
        )

    def test_batched_docs_match_the_per_item_values(self):
        tagged = Edition.objects.create(title="Tagged")
        Mark(self.owner, tagged).update(
            ShelfType.COMPLETE, visibility=0, tags=["sci-fi"]
        )
        Mark(self.other, tagged).update(
            ShelfType.COMPLETE, visibility=0, tags=["sci-fi", "epic"]
        )
        bare = Edition.objects.create(title="Bare")
        show = TVShow.objects.create(
            localized_title=[{"lang": "en", "text": "Batched Show"}]
        )
        season = TVSeason.objects.create(
            localized_title=[{"lang": "en", "text": "Batched Show Season 1"}], show=show
        )
        episode = TVEpisode.objects.create(season=season, episode_number=1)
        # The season rolls its episodes up, so this counts two distinct owners.
        Mark(self.owner, season).update(ShelfType.COMPLETE, visibility=0)
        Mark(self.other, episode).update(ShelfType.COMPLETE, visibility=0)
        podcast = Podcast.objects.create(
            localized_title=[{"lang": "en", "text": "Batched Podcast"}],
            primary_lookup_id_type=IdType.RSS,
            primary_lookup_id_value="https://example.com/batched.xml",
        )
        podcast_episode = PodcastEpisode.objects.create(
            localized_title=[{"lang": "en", "text": "Batched Episode"}],
            program=podcast,
            guid="batched-guid",
            pub_date=timezone.now(),
        )
        # The same owner on both, so the rollup must not double count.
        Mark(self.owner, podcast).update(ShelfType.COMPLETE, visibility=0)
        Mark(self.owner, podcast_episode).update(ShelfType.COMPLETE, visibility=0)

        items = [tagged, bare, season, podcast]
        expected = {
            i.pk: (
                TagManager.indexable_tags_for_item(i),
                Mark.get_mark_count_for_item(i),
            )
            for i in items
        }
        # Sanity: the batch covers tags, no tags at all, and a child rollup.
        assert len(expected[tagged.pk][0]) == 2
        assert expected[tagged.pk][1] == 2
        assert expected[bare.pk] == ([], 0)
        assert expected[season.pk][1] == 2
        assert expected[podcast.pk][1] == 1

        index, _ = self._replace_items([i.pk for i in items])
        docs = {int(d["id"]): d for d in index.replace_docs.call_args[0][0]}
        for pk, (tags, mark_count) in expected.items():
            assert docs[pk]["tag"] == tags
            assert docs[pk]["mark_count"] == mark_count

    def test_season_title_includes_the_show_without_a_per_item_lookup(self):
        show = TVShow.objects.create(
            localized_title=[{"lang": "en", "text": "Parent Show"}]
        )
        seasons = [
            TVSeason.objects.create(
                localized_title=[{"lang": "en", "text": f"Parent Show Season {n}"}],
                show=show,
            )
            for n in range(1, 4)
        ]

        index, queries = self._replace_items([s.pk for s in seasons])
        docs = index.replace_docs.call_args[0][0]
        assert all("Parent Show" in d["title"] for d in docs)

        # The N+1 signature is Django's ``.get()`` on the show FK; the
        # polymorphic batch load uses ``IN (...)`` and no LIMIT.
        offending = [
            q
            for q in queries
            if 'FROM "catalog_tvshow"' in q["sql"] and "LIMIT 21" in q["sql"]
        ]
        assert offending == [], (
            f"replace_items fired {len(offending)} per-season catalog_tvshow "
            "FK lookup(s); expected 0."
        )
