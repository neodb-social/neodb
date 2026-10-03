from types import SimpleNamespace

import numpy as np
import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test.utils import CaptureQueriesContext
from scipy.sparse import csc_matrix

from catalog.apis import _prepare_reco_items
from catalog.jobs import recommendation as recommendation_job
from catalog.jobs.recommendation import (
    BuildItemSimilarity,
    BuildUserRecommendations,
    _cosine_topk,
)
from catalog.models import (
    Edition,
    ExternalResource,
    IdType,
    Item,
    ItemCredit,
    ItemSimilarity,
    Performance,
    PerformanceProduction,
    TVShow,
    UserRecommendation,
    Work,
)
from catalog.recommendation import (
    blended_for_discover,
    compute_for_user,
    from_your_circles,
    mark_weight,
    similar_items,
    training_rewrite_map,
    user_mean_grade,
)
from common.models import SiteConfig
from journal.models import Collection, Mark, Note, Review, ShelfType
from users.models import User


@pytest.fixture(autouse=True)
def _isolate_site_config(_load_site_config):
    saved = SiteConfig.system
    SiteConfig.system = saved.model_copy()
    yield
    SiteConfig.system = saved


def _set(**kwargs):
    """Override SiteConfig.system for the duration of a test."""
    for k, v in kwargs.items():
        setattr(SiteConfig.system, k, v)


def _public_mark(identity, item, shelf=ShelfType.COMPLETE, rating=8):
    Mark(identity, item).update(shelf, "", rating, [], 0)


@pytest.mark.django_db(databases="__all__")
class TestPreferenceGate:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.user = User.register(email="g@test.com", username="g_user")
        _set(enable_recommendations=False)

    def test_off_when_master_off(self):
        _set(enable_recommendations=False)
        assert self.user.preference.show_recommendations("similar_items") is False

    def test_off_when_user_opted_out(self):
        _set(enable_recommendations=True)
        self.user.preference.disable_recommendations = True
        self.user.preference.save()
        assert self.user.preference.show_recommendations("similar_items") is False

    def test_on_when_master_on(self):
        _set(enable_recommendations=True)
        self.user.preference.disable_recommendations = False
        self.user.preference.save()
        assert self.user.preference.show_recommendations("similar_items") is True


@pytest.mark.django_db(databases="__all__")
class TestSimilarityBuilder:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=3,
            reco_min_target_marks=2,
            reco_similarity_top_k=10,
            reco_user_mark_cap=100,
            reco_user_idf_dampen=True,
        )
        # 4 users, 4 books. Build co-occurrence A-B, C-D strong; A-D weak.
        self.users = [
            User.register(email=f"s{i}@test.com", username=f"s_user{i}")
            for i in range(5)
        ]
        self.identities = [u.identity for u in self.users]
        self.book_a = Edition.objects.create(title="A")
        self.book_b = Edition.objects.create(title="B")
        self.book_c = Edition.objects.create(title="C")
        self.book_d = Edition.objects.create(title="D")
        # 3 users co-shelve A+B
        for ident in self.identities[:3]:
            _public_mark(ident, self.book_a)
            _public_mark(ident, self.book_b)
        # 3 users co-shelve C+D
        for ident in self.identities[2:5]:
            _public_mark(ident, self.book_c)
            _public_mark(ident, self.book_d)
        # 1 cross-shelving for noise
        _public_mark(self.identities[0], self.book_d)

    def test_active_items_meet_threshold(self):
        BuildItemSimilarity().run()
        sources = set(
            ItemSimilarity.objects.values_list("source_id", flat=True).distinct()
        )
        # All four books have >= 3 marks (A=3, B=3, C=3, D=4)
        assert {
            self.book_a.pk,
            self.book_b.pk,
            self.book_c.pk,
            self.book_d.pk,
        } <= sources

    def test_top_similarity_pair_is_strongest(self):
        BuildItemSimilarity().run()
        a_top = (
            ItemSimilarity.objects.filter(source=self.book_a).order_by("-score").first()
        )
        assert a_top is not None
        assert a_top.target_id == self.book_b.pk

    def test_idf_damping_softens_heavy_user(self):
        # Add a heavy user that shelves all 4 books -- their pair contribution
        # should be heavily damped vs. the non-heavy users above.
        heavy = User.register(email="h@test.com", username="h_user").identity
        for b in (self.book_a, self.book_b, self.book_c, self.book_d):
            _public_mark(heavy, b)
        BuildItemSimilarity().run()
        # A-B score should remain larger than A-C, because A and C only share
        # the heavy user (damped) while A-B has 3 distinct dedicated co-shelvers.
        ab = ItemSimilarity.objects.filter(
            source=self.book_a, target=self.book_b
        ).first()
        ac = ItemSimilarity.objects.filter(
            source=self.book_a, target=self.book_c
        ).first()
        assert ab is not None
        if ac is not None:
            assert ab.score > ac.score


@pytest.mark.django_db(databases="__all__")
class TestDiscoverableOptOut:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=2,
            reco_min_target_marks=2,
            reco_similarity_top_k=10,
            reco_user_mark_cap=100,
            reco_user_idf_dampen=False,
        )
        self.users = [
            User.register(email=f"d{i}@test.com", username=f"d_user{i}")
            for i in range(2)
        ]
        self.identities = [u.identity for u in self.users]
        self.p = Edition.objects.create(title="P")
        self.q = Edition.objects.create(title="Q")
        for ident in self.identities:
            _public_mark(ident, self.p)
            _public_mark(ident, self.q)

    def _set_discoverable(self, identity, value: bool) -> None:
        t = identity.takahe_identity
        t.discoverable = value
        t.save(update_fields=["discoverable"])

    def test_marks_skipped_when_owner_not_discoverable(self):
        # Both users opt out -> no co-occurrence at all.
        for ident in self.identities:
            self._set_discoverable(ident, False)
        BuildItemSimilarity().run()
        assert not ItemSimilarity.objects.filter(source=self.p).exists()

    def test_single_holdout_drops_cooc_below_threshold(self):
        # Only one user opts out -> threshold of 2 marks no longer met for P or Q.
        self._set_discoverable(self.identities[0], False)
        BuildItemSimilarity().run()
        assert not ItemSimilarity.objects.filter(source=self.p).exists()


@pytest.mark.django_db(databases="__all__")
class TestVisibilityRegression:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=2,
            reco_min_target_marks=2,
            reco_similarity_top_k=10,
            reco_user_mark_cap=100,
            reco_user_idf_dampen=False,
        )
        self.users = [
            User.register(email=f"v{i}@test.com", username=f"v_user{i}")
            for i in range(3)
        ]
        self.identities = [u.identity for u in self.users]
        self.x = Edition.objects.create(title="X")
        self.y = Edition.objects.create(title="Y")

    def test_private_marks_excluded(self):
        # 2 users mark X+Y privately (visibility=2)
        for ident in self.identities[:2]:
            Mark(ident, self.x).update(ShelfType.COMPLETE, "", 5, [], 2)
            Mark(ident, self.y).update(ShelfType.COMPLETE, "", 5, [], 2)
        BuildItemSimilarity().run()
        # No similarity should appear -- private marks shouldn't contribute
        assert not ItemSimilarity.objects.filter(source=self.x).exists()


@pytest.mark.django_db(databases="__all__")
class TestUserRecommendations:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=2,
            reco_min_target_marks=2,
            reco_similarity_top_k=10,
            reco_user_top_n=10,
            reco_per_user_seed_cap=50,
            reco_user_mark_cap=100,
            reco_user_active_days=30,
            reco_user_idf_dampen=False,
            reco_lazy_ttl_days=7,
        )
        self.alice = User.register(email="a@t.com", username="alice").identity
        self.bob = User.register(email="b@t.com", username="bob").identity
        self.target_user = User.register(email="t@t.com", username="target_user")
        self.target_id = self.target_user.identity
        self.b1 = Edition.objects.create(title="Sci-Fi 1")
        self.b2 = Edition.objects.create(title="Sci-Fi 2")
        self.b3 = Edition.objects.create(title="Sci-Fi 3")
        # Two strangers co-shelve b1+b2 and b1+b3, giving b1 similarity to b2 & b3.
        for ident in (self.alice, self.bob):
            _public_mark(ident, self.b1)
            _public_mark(ident, self.b2)
            _public_mark(ident, self.b3)
        BuildItemSimilarity().run()
        # Target user shelves only b1.
        _public_mark(self.target_id, self.b1)

    def test_excludes_already_shelved(self):
        rows = compute_for_user(self.target_user.pk, self.target_id.pk)
        ids = {r.item_id for r in rows}
        assert self.b1.pk not in ids
        # b2 and b3 should appear as candidates.
        assert self.b2.pk in ids or self.b3.pk in ids

    def test_batch_job_writes_rows(self):
        BuildUserRecommendations().run()
        assert UserRecommendation.objects.filter(user=self.target_user).exists()
        # Each row's item must not be one the user already shelved.
        for row in UserRecommendation.objects.filter(user=self.target_user):
            assert row.item_id != self.b1.pk


@pytest.mark.django_db(databases="__all__")
class TestSimilarItemsHelper:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=2,
            reco_min_target_marks=2,
            reco_similarity_top_k=10,
            reco_user_mark_cap=100,
            reco_user_idf_dampen=False,
        )
        self.viewers = [
            User.register(email=f"sv{i}@t.com", username=f"sv{i}").identity
            for i in range(3)
        ]
        self.src = Edition.objects.create(title="Src")
        self.t1 = Edition.objects.create(title="T1")
        self.t2 = Edition.objects.create(title="T2")
        for v in self.viewers:
            _public_mark(v, self.src)
            _public_mark(v, self.t1)
            _public_mark(v, self.t2)
        BuildItemSimilarity().run()

    def test_returns_items(self):
        out = similar_items(self.src, viewer=None, limit=5)
        assert {i.pk for i in out} >= {self.t1.pk, self.t2.pk} - {0}

    def test_excludes_shelved_for_viewer(self):
        watcher = User.register(email="w@t.com", username="watcher")
        _public_mark(watcher.identity, self.t1)
        out = similar_items(self.src, viewer=watcher, limit=5)
        assert all(i.pk != self.t1.pk for i in out)


@pytest.mark.django_db(databases="__all__")
class TestBlendedReturnsEmptyWhenDisabled:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(enable_recommendations=False)
        self.user = User.register(email="bd@t.com", username="bd_user")

    def test_empty_when_master_off(self):
        out = blended_for_discover(self.user, limit=10)
        assert out == []


@pytest.mark.django_db(databases="__all__")
class TestWishlistAsSeed:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=2,
            reco_min_target_marks=2,
            reco_similarity_top_k=10,
            reco_user_mark_cap=100,
            reco_user_idf_dampen=False,
        )
        self.users = [
            User.register(email=f"w{i}@test.com", username=f"w_user{i}")
            for i in range(2)
        ]
        self.identities = [u.identity for u in self.users]
        self.k = Edition.objects.create(title="K")
        self.l = Edition.objects.create(title="L")
        # Both users wishlist both books.
        for ident in self.identities:
            Mark(ident, self.k).update(ShelfType.WISHLIST, "", 0, [], 0)
            Mark(ident, self.l).update(ShelfType.WISHLIST, "", 0, [], 0)

    def test_wishlist_marks_train_similarity(self):
        BuildItemSimilarity().run()
        # K and L are co-wishlisted by 2 distinct users -> threshold met,
        # similarity row should exist.
        assert ItemSimilarity.objects.filter(source=self.k, target=self.l).exists()


@pytest.mark.django_db(databases="__all__")
class TestProductionRewritesToPerformance:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=2,
            reco_min_target_marks=2,
            reco_similarity_top_k=10,
            reco_user_mark_cap=100,
            reco_user_idf_dampen=False,
        )
        self.users = [
            User.register(email=f"pp{i}@test.com", username=f"pp_user{i}")
            for i in range(2)
        ]
        self.identities = [u.identity for u in self.users]
        # Performance with two Productions; both users shelve one Production each.
        self.show = Performance.objects.create(title="Hamilton")
        self.prod_a = PerformanceProduction.objects.create(title="2015 Broadway")
        self.prod_a.show = self.show
        self.prod_a.save()
        self.prod_b = PerformanceProduction.objects.create(title="2024 Tour")
        self.prod_b.show = self.show
        self.prod_b.save()
        # Another Performance that one user shelved (Performance-direct).
        self.peer = Performance.objects.create(title="Other Show")
        _public_mark(self.identities[0], self.prod_a)
        _public_mark(self.identities[1], self.prod_b)
        # Both also mark the peer Performance so co-occurrence is non-trivial.
        _public_mark(self.identities[0], self.peer)
        _public_mark(self.identities[1], self.peer)

    def test_production_marks_aggregate_to_performance(self):
        BuildItemSimilarity().run()
        # Hamilton (Performance) should appear as a source even though
        # nobody marked it directly -- two distinct users shelved its
        # Productions, meeting min_source_marks=2 after rewrite.
        assert ItemSimilarity.objects.filter(source=self.show).exists()

    def test_productions_are_not_recommended(self):
        BuildItemSimilarity().run()
        # PerformanceProduction must never appear as a target.
        target_ids = set(
            ItemSimilarity.objects.values_list("target_id", flat=True).distinct()
        )
        assert self.prod_a.pk not in target_ids
        assert self.prod_b.pk not in target_ids


@pytest.mark.django_db(databases="__all__")
class TestMergedItemsInTraining:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=2,
            reco_min_target_marks=2,
            reco_similarity_top_k=10,
            reco_user_top_n=10,
            reco_per_user_seed_cap=50,
            reco_user_mark_cap=100,
            reco_user_idf_dampen=False,
        )
        self.identities = [
            User.register(email=f"mg{i}@t.com", username=f"mg_user{i}").identity
            for i in range(2)
        ]
        self.old = Edition.objects.create(title="Old edition")
        self.survivor = Edition.objects.create(title="Survivor")
        self.peer = Edition.objects.create(title="Peer")
        _public_mark(self.identities[0], self.old)
        _public_mark(self.identities[0], self.peer)
        _public_mark(self.identities[1], self.survivor)
        _public_mark(self.identities[1], self.peer)
        # marks stay on the merged item, as when the journal move was skipped
        self.old.merge_to(self.survivor)

    def test_merged_marks_count_toward_survivor(self):
        BuildItemSimilarity().run()
        # survivor reaches the threshold of 2 only through the merged mark
        assert ItemSimilarity.objects.filter(
            source=self.survivor, target=self.peer
        ).exists()
        assert ItemSimilarity.objects.filter(
            source=self.peer, target=self.survivor
        ).exists()
        assert not ItemSimilarity.objects.filter(source=self.old).exists()
        assert not ItemSimilarity.objects.filter(target=self.old).exists()

    def test_chain_resolves_to_final(self):
        final = Edition.objects.create(title="Final")
        self.survivor.merge_to(final)
        m = training_rewrite_map()
        assert m[self.old.pk] == final.pk
        assert m[self.survivor.pk] == final.pk
        assert not set(m) & set(m.values())

    def test_merge_into_deleted_item_is_dropped(self):
        # a second owner so the merged item would be active on its own
        _public_mark(self.identities[1], self.old)
        Item.objects.filter(pk=self.survivor.pk).update(is_deleted=True)
        assert self.old.pk not in training_rewrite_map()
        BuildItemSimilarity().run()
        assert not ItemSimilarity.objects.filter(source=self.old).exists()
        assert not ItemSimilarity.objects.filter(target=self.old).exists()

    def test_production_of_merged_performance(self):
        show_old = Performance.objects.create(title="Show old")
        show_new = Performance.objects.create(title="Show new")
        prod = PerformanceProduction.objects.create(title="Staging")
        prod.show = show_old
        prod.save()
        prod_old = PerformanceProduction.objects.create(title="Staging dup")
        prod_old.show = show_new
        prod_old.save()
        show_old.merge_to(show_new)
        prod_old.merge_to(prod)
        m = training_rewrite_map()
        assert m[prod.pk] == show_new.pk
        assert m[prod_old.pk] == show_new.pk
        assert m[show_old.pk] == show_new.pk
        assert not set(m) & set(m.values())

    def test_production_of_deleted_performance_is_dropped(self):
        show = Performance.objects.create(title="Gone show")
        prod = PerformanceProduction.objects.create(title="Gone staging")
        prod.show = show
        prod.save()
        Item.objects.filter(pk=show.pk).update(is_deleted=True)
        m = training_rewrite_map()
        assert prod.pk not in m
        assert show.pk not in m.values()

    def test_shelved_merged_edition_excludes_survivor(self):
        BuildItemSimilarity().run()
        viewer = User.register(email="mgv@t.com", username="mg_viewer")
        _public_mark(viewer.identity, self.peer)
        _public_mark(viewer.identity, self.old)
        ids = {r.item_id for r in compute_for_user(viewer.pk, viewer.identity.pk)}
        assert self.survivor.pk not in ids
        similar = {i.pk for i in similar_items(self.peer, viewer=viewer, limit=10)}
        assert self.survivor.pk not in similar

    def test_unrelated_viewer_still_gets_survivor(self):
        BuildItemSimilarity().run()
        viewer = User.register(email="mgu@t.com", username="mg_other")
        _public_mark(viewer.identity, self.peer)
        ids = {r.item_id for r in compute_for_user(viewer.pk, viewer.identity.pk)}
        assert self.survivor.pk in ids


@pytest.mark.django_db(databases="__all__")
class TestExcludedTargetClasses:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=2,
            reco_min_target_marks=2,
            reco_similarity_top_k=10,
            reco_user_mark_cap=100,
            reco_user_idf_dampen=False,
        )
        self.users = [
            User.register(email=f"ex{i}@test.com", username=f"ex_user{i}")
            for i in range(2)
        ]
        self.identities = [u.identity for u in self.users]
        self.show = TVShow.objects.create(title="A Show")
        self.peer = Edition.objects.create(title="A Book")
        for ident in self.identities:
            _public_mark(ident, self.show)
            _public_mark(ident, self.peer)

    def test_tvshow_never_recommended(self):
        BuildItemSimilarity().run()
        target_ids = set(
            ItemSimilarity.objects.values_list("target_id", flat=True).distinct()
        )
        assert self.show.pk not in target_ids


@pytest.mark.django_db(databases="__all__")
class TestSiblingEditionExclusion:
    """Precompute drops sibling editions of shelved items; request path allows dupes."""

    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=2,
            reco_min_target_marks=2,
            reco_similarity_top_k=10,
            reco_user_top_n=10,
            reco_per_user_seed_cap=50,
            reco_user_mark_cap=100,
            reco_user_active_days=30,
            reco_user_idf_dampen=False,
            reco_lazy_ttl_days=7,
        )
        self.strangers = [
            User.register(email=f"sib{i}@t.com", username=f"sib{i}").identity
            for i in range(2)
        ]
        self.src = Edition.objects.create(title="Src")
        # A Work with two sibling editions.
        self.work = Work.objects.create(title="The Work")
        self.e1 = Edition.objects.create(title="Edition One")
        self.e2 = Edition.objects.create(title="Edition Two")
        self.work.editions.add(self.e1, self.e2)
        # A standalone target with no siblings, as a control.
        self.other = Edition.objects.create(title="Other")
        # Strangers co-shelve src with e2 and with other -> similarity built so
        # both e2 and other are valid recommendation targets for src.
        for ident in self.strangers:
            _public_mark(ident, self.src)
            _public_mark(ident, self.e2)
            _public_mark(ident, self.other)
        BuildItemSimilarity().run()

    def test_compute_for_user_excludes_sibling_edition(self):
        target = User.register(email="sibt@t.com", username="sibt")
        # Seed with src so similarity surfaces e2 and other, and mark e1.
        _public_mark(target.identity, self.src)
        _public_mark(target.identity, self.e1)
        ids = {r.item_id for r in compute_for_user(target.pk, target.identity.pk)}
        assert self.e2.pk not in ids  # sibling of marked e1 -> excluded
        assert self.other.pk in ids  # unrelated target still recommended

    def test_batch_job_excludes_sibling_edition(self):
        target = User.register(email="sibb@t.com", username="sibb")
        _public_mark(target.identity, self.src)
        _public_mark(target.identity, self.e1)
        BuildUserRecommendations().run()
        rows = UserRecommendation.objects.filter(user=target)
        item_ids = {r.item_id for r in rows}
        assert self.e2.pk not in item_ids  # sibling excluded by the precompute
        assert self.other.pk in item_ids

    def test_request_path_allows_sibling_dupe(self):
        # similar_items is request-time and deliberately does NOT do sibling
        # suppression; marking e1 still lets its sibling e2 surface as a dupe.
        watcher = User.register(email="sibw@t.com", username="sibw")
        _public_mark(watcher.identity, self.e1)
        ids = {i.pk for i in similar_items(self.src, viewer=watcher, limit=10)}
        assert self.e2.pk in ids


@pytest.mark.django_db(databases="__all__")
class TestRecoItemsExternalResourcesNoNPlusOne:
    """N+1 on /similar and /me/recommendations.

    Reco items come from similar_items()/blended_for_discover(), which -- unlike
    the search path's query_index -- do not pre-hydrate external_resources, so
    ItemSchema serialization fired one ``catalog_externalresource WHERE
    item_id = %s`` query per item. ``_prepare_reco_items`` must batch-prefetch
    them.
    """

    def _items_with_external_resources(self, n: int = 3) -> list:
        created = []
        for i in range(n):
            book = Edition.objects.create(title=f"Reco {i}")
            ExternalResource.objects.create(
                item=book,
                id_type=IdType.RSS,
                id_value=f"reco-{i}",
                url=f"https://example.com/reco-{i}",
            )
            created.append(book)
        # Fresh polymorphic instances with an empty prefetch cache, mirroring
        # what similar_items()/blended_for_discover() hand back.
        return list(Item.objects.filter(pk__in=[b.pk for b in created]))

    def test_external_resources_prefetched(self):
        items = self._items_with_external_resources()
        request = SimpleNamespace(user=AnonymousUser())
        _prepare_reco_items(request, items)
        # Reading external_resources (as ItemSchema does) must now be served
        # from the prefetch cache without per-item queries.
        with CaptureQueriesContext(connection) as ctx:
            for it in items:
                for res in it.external_resources.all():
                    _ = res.url
        offending = [
            q
            for q in ctx.captured_queries
            if 'FROM "catalog_externalresource"' in q["sql"]
        ]
        assert offending == [], (
            f"reading reco items' external_resources fired {len(offending)} "
            "query(ies); expected 0 (prefetched). First offending SQL: "
            f"{offending[0]['sql'] if offending else 'n/a'}"
        )


@pytest.mark.django_db(databases="__all__")
class TestFromYourCircles:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(enable_recommendations=True, reco_circles_window_days=14)
        self.viewer = User.register(email="c0@test.com", username="c_viewer")
        self.friends = [
            User.register(email=f"c{i + 1}@test.com", username=f"c_friend{i}")
            for i in range(2)
        ]
        for f in self.friends:
            self.viewer.identity.follow(f.identity, True)
        self.book_a = Edition.objects.create(title="Circles A")
        self.book_b = Edition.objects.create(title="Circles B")
        for f in self.friends:
            _public_mark(f.identity, self.book_a)
            _public_mark(f.identity, self.book_b)

    def test_returns_followee_items(self):
        items = from_your_circles(self.viewer)
        assert {i.pk for i in items} == {self.book_a.pk, self.book_b.pk}

    def test_excludes_viewer_shelved(self):
        _public_mark(self.viewer.identity, self.book_a)
        items = from_your_circles(self.viewer)
        assert {i.pk for i in items} == {self.book_b.pk}

    def test_shelved_exclusion_uses_subquery(self):
        # Regression: the viewer's shelved items must be
        # excluded via a subquery, not inlined as one parameter per item.
        _public_mark(self.viewer.identity, self.book_a)
        with CaptureQueriesContext(connection) as ctx:
            from_your_circles(self.viewer)
        agg = [q["sql"] for q in ctx.captured_queries if "COUNT(DISTINCT" in q["sql"]]
        assert len(agg) == 1
        assert "IN (SELECT" in agg[0], (
            f"shelved-item exclusion is not a subquery: {agg[0]}"
        )
        # ShelfMemberManager's default annotations must not leak in either.
        assert 'FROM "journal_rating"' not in agg[0]
        assert 'FROM "journal_comment"' not in agg[0]

    def _count_aggregates(self) -> tuple[list, int]:
        with CaptureQueriesContext(connection) as ctx:
            items = from_your_circles(self.viewer)
        agg = [q for q in ctx.captured_queries if "COUNT(DISTINCT" in q["sql"]]
        return items, len(agg)

    def test_second_call_reuses_cached_ranking(self):
        first, n = self._count_aggregates()
        assert n == 1
        second, n = self._count_aggregates()
        assert n == 0
        assert [i.pk for i in second] == [i.pk for i in first]

    def test_mark_after_cache_is_still_excluded(self):
        from_your_circles(self.viewer)
        _public_mark(self.viewer.identity, self.book_a)
        items, n = self._count_aggregates()
        assert n == 0
        assert {i.pk for i in items} == {self.book_b.pk}

    def test_new_followee_mark_waits_for_ttl(self):
        from_your_circles(self.viewer)
        book_c = Edition.objects.create(title="Circles C")
        _public_mark(self.friends[0].identity, book_c)
        items = from_your_circles(self.viewer)
        assert book_c.pk not in {i.pk for i in items}

    def test_first_follow_is_not_hidden_by_cache(self):
        loner = User.register(email="c9@test.com", username="c_loner")
        assert from_your_circles(loner) == []
        loner.identity.follow(self.friends[0].identity, True)
        items = from_your_circles(loner)
        assert {i.pk for i in items} == {self.book_a.pk, self.book_b.pk}


class TestCosineTopk:
    def _run(self, shrinkage: float) -> dict[int, list[tuple[int, float]]]:
        # users x items; item 0 shares 2 users with item 1, 1 user with item 2
        m = csc_matrix(np.array([[1, 1, 0], [2, 2, 1], [0, 0, 3]], dtype=np.float32))
        mask = np.array([True, True, True])
        return {
            src: list(zip(cols.tolist(), vals.tolist()))
            for src, cols, vals in _cosine_topk(
                m, np.array([0, 1, 2]), mask, 5, shrinkage
            )
        }

    def test_scores_and_blocks(self, monkeypatch):
        n0 = (1 + 4) ** 0.5
        n2 = (1 + 9) ** 0.5
        expected = {
            0: [(1, 1.0 * 2 / 7), (2, 2 / (n0 * n2) * 1 / 6)],
            1: [(0, 1.0 * 2 / 7), (2, 2 / (n0 * n2) * 1 / 6)],
            2: [(0, 2 / (n0 * n2) * 1 / 6), (1, 2 / (n0 * n2) * 1 / 6)],
        }
        for block in (256, 1):
            monkeypatch.setattr(recommendation_job, "_SOURCE_BLOCK", block)
            got = self._run(5.0)
            assert got.keys() == expected.keys()
            for src, rows in expected.items():
                assert dict(got[src]) == pytest.approx(dict(rows), rel=1e-5)
                scores = [v for _, v in got[src]]
                assert scores == sorted(scores, reverse=True)
        assert self._run(0.0)[0][0] == (1, pytest.approx(1.0, rel=1e-5))


class TestMarkWeight:
    BOOSTS = {"rating_weight": 0.5, "review_boost": 0.5, "comment_note_boost": 0.25}

    def _w(self, grade, mean, review=False, comment=False, note=False, **kw):
        return mark_weight(grade, mean, review, comment, note, **(self.BOOSTS | kw))

    def test_unrated_is_neutral(self):
        assert self._w(None, 8.0) == 1.0
        assert self._w(9, None) == 1.0

    def test_rating_is_centered_on_user_mean(self):
        assert self._w(10, 8.0) == pytest.approx(1 + 0.5 * 2 / 4.5)
        assert self._w(6, 8.0) == pytest.approx(1 - 0.5 * 2 / 4.5)
        assert self._w(8, 8.0) == pytest.approx(1.0)
        assert self._w(10, 8.0, rating_weight=0) == 1.0

    def test_floor_and_cap(self):
        assert self._w(1, 10.0) == pytest.approx(0.2)
        assert self._w(10, 1.0, review=True, comment=True, note=True) == 2.0
        assert self._w(
            None, None, review=True, comment=True, note=True, review_boost=1
        ) == pytest.approx(2.0)

    def test_annotations_raise_weight(self):
        assert self._w(None, None, review=True) == pytest.approx(1.5)
        assert self._w(None, None, comment=True, note=True) == pytest.approx(1.5)
        assert self._w(None, None, review=True) > self._w(None, None, comment=True)

    def test_user_mean_needs_three_grades(self):
        assert user_mean_grade([8, None, 9]) is None
        assert user_mean_grade([3, 9, 9, None]) == pytest.approx(7.0)


@pytest.mark.django_db(databases="__all__")
class TestTrainingMarkSignals:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_rating_weight=0.5,
            reco_review_boost=0.5,
            reco_comment_note_boost=0.25,
        )
        self.identity = User.register(email="ms@t.com", username="ms_user").identity
        self.items = [Edition.objects.create(title=f"MS {i}") for i in range(5)]
        a, b, c, d, e = self.items
        _public_mark(self.identity, a, rating=10)
        _public_mark(self.identity, b, rating=3)
        Mark(self.identity, c).update(ShelfType.COMPLETE, "liked it", 8, [], 0)
        _public_mark(self.identity, d, rating=8)
        _public_mark(self.identity, e, rating=0)
        Note.objects.create(item=d, owner=self.identity, content="n", visibility=0)
        Review.update_item_review(b, self.identity, "private", "body", visibility=2)
        Review.update_item_review(e, self.identity, "public", "body", visibility=0)

    def test_weights_from_public_signals(self):
        pk = self.identity.pk
        ids = [i.pk for i in self.items]
        marks = BuildItemSimilarity()._weigh_marks({pk: ids}, {})[pk]
        assert list(marks.items) == ids
        mean = (10 + 3 + 8 + 8) / 4
        factor = 1 + 0.5 * (8 - mean) / 4.5
        expected = [
            1 + 0.5 * (10 - mean) / 4.5,
            1 + 0.5 * (3 - mean) / 4.5,  # private review is ignored
            factor * 1.25,  # comment
            factor * 1.25,  # note
            1.5,  # unrated, public review
        ]
        assert list(marks.weights) == pytest.approx(expected, rel=1e-5)


@pytest.mark.django_db(databases="__all__")
class TestSimilarityCosine:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=1,
            reco_min_target_marks=1,
            reco_similarity_top_k=10,
            reco_user_mark_cap=100,
            reco_user_idf_dampen=False,
            reco_review_boost=0.5,
        )

    def _users(self, prefix: str, n: int) -> list:
        return [
            User.register(email=f"{prefix}{i}@t.com", username=f"{prefix}{i}").identity
            for i in range(n)
        ]

    def _score(self, src, tgt) -> float:
        # shelf weighs 1 in the blend and nothing else links these items
        return ItemSimilarity.objects.get(
            source=src, target=tgt, method=ItemSimilarity.METHOD_BLENDED
        ).score

    def _pairs(self) -> tuple:
        p, q, x, y = (Edition.objects.create(title=t) for t in "PQXY")
        for ident in self._users("cs", 2):
            _public_mark(ident, p, rating=0)
            _public_mark(ident, q, rating=0)
        for ident in self._users("cl", 10):
            _public_mark(ident, x, rating=0)
            _public_mark(ident, y, rating=0)
        return p, q, x, y

    def test_shrinkage_favours_well_supported_pairs(self):
        # damping must not change the scale shrinkage works in
        _set(reco_similarity_shrinkage=5.0, reco_user_idf_dampen=True)
        p, q, x, y = self._pairs()
        BuildItemSimilarity().run()
        # both pairs have cosine 1; shrinkage keeps the 2-user pair lower
        assert self._score(p, q) == pytest.approx(2 / 7, rel=1e-5)
        assert self._score(x, y) == pytest.approx(10 / 15, rel=1e-5)
        scores = list(ItemSimilarity.objects.values_list("score", flat=True))
        assert scores and all(0 <= s < 1 for s in scores)

    def test_zero_shrinkage_keeps_cosine(self):
        _set(reco_similarity_shrinkage=0.0, reco_user_idf_dampen=True)
        p, q, x, y = self._pairs()
        BuildItemSimilarity().run()
        assert self._score(p, q) == pytest.approx(1.0, rel=1e-5)
        assert self._score(x, y) == pytest.approx(1.0, rel=1e-5)

    def test_batched_weighing_mid_stream(self, monkeypatch):
        monkeypatch.setattr(recommendation_job, "_WEIGH_OWNER_BATCH", 1)
        self.test_shrinkage_favours_well_supported_pairs()

    def test_review_backed_mark_outweighs_bare_mark(self):
        _set(reco_similarity_shrinkage=0.0)
        s, a, b = (Edition.objects.create(title=t) for t in "SAB")
        u1, u2, u3, u4 = self._users("rv", 4)
        _public_mark(u1, s, rating=0)
        _public_mark(u1, a, rating=0)
        Review.update_item_review(a, u1, "great", "body", visibility=0)
        _public_mark(u2, s, rating=0)
        _public_mark(u2, b, rating=0)
        # single-mark users still count toward each item's norm
        _public_mark(u3, a, rating=0)
        _public_mark(u4, b, rating=0)
        BuildItemSimilarity().run()
        assert self._score(s, a) == pytest.approx(1.5 / (2**0.5 * 3.25**0.5), rel=1e-5)
        assert self._score(s, b) == pytest.approx(0.5, rel=1e-5)


@pytest.mark.django_db(databases="__all__")
class TestSeedCombiner:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_user_top_n=10,
            reco_per_user_seed_cap=50,
            reco_rating_weight=0.5,
            reco_review_boost=0.5,
            reco_comment_note_boost=0.25,
        )
        self.user = User.register(email="sc@t.com", username="sc_user")
        self.identity = self.user.identity
        self.t1 = Edition.objects.create(title="T1")
        self.t2 = Edition.objects.create(title="T2")

    def _seed(self, title: str, rating: int = 0, **sims: float) -> Edition:
        seed = Edition.objects.create(title=title)
        _public_mark(self.identity, seed, rating=rating)
        for attr, score in sims.items():
            ItemSimilarity.objects.create(
                source=seed,
                target=getattr(self, attr),
                score=score,
                method=ItemSimilarity.METHOD_BLENDED,
            )
        return seed

    def _scores(self) -> dict[int, float]:
        return {
            r.item_id: r.score for r in compute_for_user(self.user.pk, self.identity.pk)
        }

    def test_rating_centering_flips_ranking(self):
        self._seed("low", rating=3, t1=0.5)
        self._seed("high", rating=10, t2=0.4)
        self._seed("filler 1", rating=9)
        self._seed("filler 2", rating=9)
        scores = self._scores()
        assert scores[self.t2.pk] > scores[self.t1.pk]
        _set(reco_rating_weight=0.0)
        scores = self._scores()
        assert scores[self.t1.pk] > scores[self.t2.pk]

    def test_too_few_grades_are_neutral(self):
        self._seed("low", rating=2, t1=0.5)
        self._seed("high", rating=10, t2=0.4)
        scores = self._scores()
        assert scores[self.t1.pk] == pytest.approx(0.5)
        assert scores[self.t2.pk] == pytest.approx(0.4)

    def test_review_backed_seed_outweighs_bare_seed(self):
        reviewed = self._seed("reviewed", t1=0.4)
        self._seed("bare", t2=0.45)
        Review.update_item_review(reviewed, self.identity, "t", "body", visibility=0)
        scores = self._scores()
        assert scores[self.t1.pk] == pytest.approx(0.6)
        assert scores[self.t1.pk] > scores[self.t2.pk]

    def test_review_on_rewrite_target_weighs_seed(self):
        show = Performance.objects.create(title="Show")
        staging = PerformanceProduction.objects.create(title="Staging")
        staging.show = show
        staging.save()
        _public_mark(self.identity, staging, rating=0)
        ItemSimilarity.objects.create(
            source=show,
            target=self.t1,
            score=0.4,
            method=ItemSimilarity.METHOD_BLENDED,
        )
        self._seed("bare", t2=0.45)
        Review.update_item_review(show, self.identity, "t", "body", visibility=0)
        scores = self._scores()
        assert scores[self.t1.pk] == pytest.approx(0.6)
        assert scores[self.t1.pk] > scores[self.t2.pk]

    def test_few_strong_seeds_beat_many_weak_ones(self):
        for i in range(3):
            self._seed(f"strong {i}", t1=0.5)
        for i in range(20):
            self._seed(f"weak {i}", t2=0.1)
        scores = self._scores()
        assert scores[self.t1.pk] == pytest.approx(1.5)
        assert scores[self.t2.pk] == pytest.approx(0.5)

    def test_seed_ids_are_the_strongest(self):
        # strongest seeds are marked first, so they are the oldest
        seeds = [self._seed(f"s{i}", t1=0.5 - 0.1 * i) for i in range(5)]
        rows = compute_for_user(self.user.pk, self.identity.pk)
        row = next(r for r in rows if r.item_id == self.t1.pk)
        assert row.seed_item_ids == [s.pk for s in seeds[:3]]
        assert row.score == pytest.approx(0.5 + 0.4 + 0.3 + 0.2 + 0.1)


@pytest.mark.django_db(databases="__all__")
class TestFeatureSimilarity:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=3,
            reco_min_target_marks=3,
            reco_similarity_top_k=10,
            reco_max_feature_items=500,
        )
        self.users = [
            User.register(email=f"ft{i}@t.com", username=f"ft_user{i}").identity
            for i in range(2)
        ]
        self.a, self.b, self.c, self.d = (
            Edition.objects.create(title=f"Feature {t}") for t in "ABCD"
        )

    def _pairs(self) -> set[tuple[int, int]]:
        # each test exercises one method, and only blended rows are stored
        assert set(ItemSimilarity.objects.values_list("method", flat=True)) <= {
            ItemSimilarity.METHOD_BLENDED
        }
        return set(ItemSimilarity.objects.values_list("source_id", "target_id"))

    def _tag(self, identity, item, tags, visibility=0):
        identity.tag_manager.tag_item(item, tags, visibility)

    def test_shared_tags_link_items(self):
        u0, u1 = self.users
        for item in (self.a, self.b):
            self._tag(u0, item, ["space", "robots"])
            self._tag(u1, item, ["#Space"])
        self._tag(u0, self.c, ["cooking"])
        self._tag(u0, self.d, ["cooking"])
        BuildItemSimilarity().run()
        pairs = self._pairs()
        assert {(self.a.pk, self.b.pk), (self.b.pk, self.a.pk)} <= pairs
        assert (self.c.pk, self.d.pk) in pairs
        assert (self.a.pk, self.c.pk) not in pairs

    def test_private_tag_does_not_count(self):
        self._tag(self.users[0], self.a, ["secret"], visibility=2)
        self._tag(self.users[0], self.b, ["secret"], visibility=2)
        BuildItemSimilarity().run()
        assert not self._pairs()

    def test_hub_feature_is_ignored(self):
        _set(reco_max_feature_items=2)
        for item in (self.a, self.b, self.c):
            self._tag(self.users[0], item, ["everything"])
        self._tag(self.users[0], self.a, ["pair"])
        self._tag(self.users[0], self.b, ["pair"])
        BuildItemSimilarity().run()
        pairs = self._pairs()
        assert (self.a.pk, self.b.pk) in pairs
        assert (self.a.pk, self.c.pk) not in pairs

    def _collection(self, identity, items, visibility=0):
        collection = Collection.objects.create(
            owner=identity, title="Shelf", brief="", visibility=visibility
        )
        for item in items:
            collection.append_item(item)

    def test_collection_links_items(self):
        self._collection(self.users[0], [self.a, self.b])
        self._collection(self.users[1], [self.c, self.d], visibility=2)
        BuildItemSimilarity().run()
        pairs = self._pairs()
        assert pairs == {(self.a.pk, self.b.pk), (self.b.pk, self.a.pk)}

    def test_non_discoverable_collection_is_ignored(self):
        t = self.users[0].takahe_identity
        t.discoverable = False
        t.save(update_fields=["discoverable"])
        self._collection(self.users[0], [self.a, self.b])
        BuildItemSimilarity().run()
        assert not self._pairs()

    def test_credit_links_books_by_same_author(self):
        for item in (self.a, self.b, self.c, self.d):
            _public_mark(self.users[0], item, rating=0)
        ItemCredit.objects.create(item=self.a, role="author", name="Ursula Le Guin")
        ItemCredit.objects.create(item=self.b, role="author", name=" ursula le guin")
        ItemCredit.objects.create(item=self.c, role="publisher", name="Ace")
        ItemCredit.objects.create(item=self.d, role="publisher", name="Ace")
        BuildItemSimilarity().run()
        pairs = self._pairs()
        assert pairs == {(self.a.pk, self.b.pk), (self.b.pk, self.a.pk)}

    def test_credit_ignores_marks_of_non_discoverable_owners(self):
        t = self.users[0].takahe_identity
        t.discoverable = False
        t.save(update_fields=["discoverable"])
        for item in (self.a, self.b):
            _public_mark(self.users[0], item, rating=0)
            ItemCredit.objects.create(item=item, role="author", name="Hidden")
        BuildItemSimilarity().run()
        assert not ItemSimilarity.objects.exists()

    def test_credit_scope_follows_merged_marks(self):
        # the only mark on the survivor's line sits on a merged edition
        _public_mark(self.users[0], self.a, rating=0)
        self.a.merge_to(self.b)
        _public_mark(self.users[1], self.c, rating=0)
        for item in (self.b, self.c):
            ItemCredit.objects.create(item=item, role="author", name="Same")
        BuildItemSimilarity().run()
        assert self._pairs() == {(self.b.pk, self.c.pk), (self.c.pk, self.b.pk)}

    def test_credit_needs_a_public_mark(self):
        ItemCredit.objects.create(item=self.a, role="author", name="Anon")
        ItemCredit.objects.create(item=self.b, role="author", name="Anon")
        BuildItemSimilarity().run()
        assert not self._pairs()

    def test_merged_item_features_count_toward_final(self):
        self._tag(self.users[0], self.a, ["moon"])
        self._tag(self.users[0], self.c, ["moon"])
        self.a.merge_to(self.b)
        BuildItemSimilarity().run()
        pairs = self._pairs()
        assert pairs == {(self.b.pk, self.c.pk), (self.c.pk, self.b.pk)}


@pytest.mark.django_db(databases="__all__")
class TestBlendedSimilarity:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=2,
            reco_min_target_marks=2,
            reco_similarity_top_k=10,
            reco_user_top_n=10,
            reco_per_user_seed_cap=50,
            reco_user_idf_dampen=False,
            reco_similarity_shrinkage=0.0,
            reco_tag_weight=0.5,
            reco_collection_weight=0.5,
            reco_content_weight=0.3,
        )
        self.users = [
            User.register(email=f"bl{i}@t.com", username=f"bl_user{i}").identity
            for i in range(2)
        ]
        self.a, self.b, self.c = (
            Edition.objects.create(title=f"Blend {t}") for t in "ABC"
        )

    def _score(self, src, tgt, method: int) -> float | None:
        row = ItemSimilarity.objects.filter(
            source=src, target=tgt, method=method
        ).first()
        return row.score if row else None

    def test_blend_weights_methods(self):
        for ident in self.users:
            _public_mark(ident, self.a, rating=0)
            _public_mark(ident, self.b, rating=0)
        for item in (self.a, self.b, self.c):
            self.users[0].tag_manager.tag_item(item, ["noir"], 0)
        BuildItemSimilarity().run()
        # shelf cosine of a and b is 1, tag cosine of every pair is 1
        blended_ab = self._score(self.a, self.b, ItemSimilarity.METHOD_BLENDED)
        blended_ac = self._score(self.a, self.c, ItemSimilarity.METHOD_BLENDED)
        assert blended_ab == pytest.approx(1.0 + 0.5, rel=1e-5)
        assert blended_ac == pytest.approx(0.5, rel=1e-5)
        _set(reco_tag_weight=0.2)
        BuildItemSimilarity().run()
        blended_ac = self._score(self.a, self.c, ItemSimilarity.METHOD_BLENDED)
        assert blended_ac == pytest.approx(0.2, rel=1e-5)
        assert set(ItemSimilarity.objects.values_list("method", flat=True)) == {
            ItemSimilarity.METHOD_BLENDED
        }

    def test_rebuild_drops_rows_of_other_methods(self):
        for ident in self.users:
            _public_mark(ident, self.a, rating=0)
            _public_mark(ident, self.b, rating=0)
        # left behind by older builds, for a covered and an uncovered source
        for src, tgt in ((self.a, self.b), (self.c, self.a)):
            ItemSimilarity.objects.create(
                source=src,
                target=tgt,
                score=0.9,
                method=ItemSimilarity.METHOD_SHELF_COOC,
            )
        BuildItemSimilarity().run()
        assert set(
            ItemSimilarity.objects.values_list("source_id", "target_id", "method")
        ) == {
            (self.a.pk, self.b.pk, ItemSimilarity.METHOD_BLENDED),
            (self.b.pk, self.a.pk, ItemSimilarity.METHOD_BLENDED),
        }

    def test_serving_reads_only_blended_rows(self):
        ItemSimilarity.objects.create(
            source=self.a,
            target=self.b,
            score=0.9,
            method=ItemSimilarity.METHOD_SHELF_COOC,
        )
        viewer = User.register(email="blv@t.com", username="bl_viewer")
        _public_mark(viewer.identity, self.a, rating=0)
        assert similar_items(self.a) == []
        assert compute_for_user(viewer.pk, viewer.identity.pk) == []
        ItemSimilarity.objects.create(
            source=self.a,
            target=self.c,
            score=0.4,
            method=ItemSimilarity.METHOD_BLENDED,
        )
        assert [i.pk for i in similar_items(self.a)] == [self.c.pk]
        rows = compute_for_user(viewer.pk, viewer.identity.pk)
        assert [r.item_id for r in rows] == [self.c.pk]

    def test_cold_item_gets_credit_neighbours(self):
        _public_mark(self.users[0], self.a, rating=0)
        _public_mark(self.users[1], self.b, rating=0)
        for item in (self.a, self.b):
            ItemCredit.objects.create(item=item, role="author", name="Same Author")
        BuildItemSimilarity().run()
        assert set(ItemSimilarity.objects.values_list("method", flat=True)) == {
            ItemSimilarity.METHOD_BLENDED
        }
        # one mark each is below the shelf threshold, credits alone link them
        assert [i.pk for i in similar_items(self.a)] == [self.b.pk]
        assert self._score(
            self.a, self.b, ItemSimilarity.METHOD_BLENDED
        ) == pytest.approx(0.3, rel=1e-5)

    def test_writer_refuses_a_source_twice(self):
        writer = recommendation_job._SimilarityWriter({}, 10)
        empty = (np.array([], dtype=np.int64), np.array([], dtype=np.float32))
        writer.add(self.a.pk, {ItemSimilarity.METHOD_TAG_COOC: empty})
        with pytest.raises(RuntimeError):
            writer.add(self.a.pk, {ItemSimilarity.METHOD_CONTENT: empty})

    def test_rebuild_prunes_sources_without_rows(self):
        self.test_cold_item_gets_credit_neighbours()
        ItemCredit.objects.filter(item=self.b).delete()
        BuildItemSimilarity().run()
        assert not ItemSimilarity.objects.exists()
