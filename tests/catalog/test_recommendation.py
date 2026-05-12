import pytest

from catalog.jobs.recommendation import BuildItemSimilarity, BuildUserRecommendations
from catalog.models import (
    Edition,
    ItemSimilarity,
    Performance,
    PerformanceProduction,
    TVShow,
    UserRecommendation,
)
from catalog.recommendation import (
    blended_for_discover,
    compute_for_user,
    similar_items,
)
from common.models import SiteConfig
from journal.models import Mark, ShelfType
from users.models import User


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
        _set(
            enable_recommendations=False,
            enable_reco_similar_items=False,
            enable_reco_for_you=False,
            enable_reco_from_circles=False,
        )

    def test_off_when_master_off(self):
        _set(enable_recommendations=False, enable_reco_similar_items=True)
        assert self.user.preference.show_recommendations("similar_items") is False

    def test_off_when_kind_off(self):
        _set(enable_recommendations=True, enable_reco_similar_items=False)
        assert self.user.preference.show_recommendations("similar_items") is False

    def test_off_when_user_opted_out(self):
        _set(enable_recommendations=True, enable_reco_similar_items=True)
        self.user.preference.disable_recommendations = True
        self.user.preference.save()
        assert self.user.preference.show_recommendations("similar_items") is False

    def test_on_when_all_enabled(self):
        _set(enable_recommendations=True, enable_reco_similar_items=True)
        self.user.preference.disable_recommendations = False
        self.user.preference.save()
        assert self.user.preference.show_recommendations("similar_items") is True


@pytest.mark.django_db(databases="__all__")
class TestSimilarityBuilder:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            enable_reco_similar_items=True,
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
            enable_reco_for_you=True,
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
        _set(
            enable_recommendations=False,
            enable_reco_for_you=False,
            enable_reco_from_circles=False,
        )
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
