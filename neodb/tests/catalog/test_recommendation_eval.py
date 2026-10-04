import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from catalog.evaluation import DiversityLookups, evaluate, parse_overrides
from catalog.jobs.recommendation import BuildItemSimilarity
from catalog.models import (
    Edition,
    ItemCredit,
    ItemSimilarity,
    UserRecommendation,
    Work,
)
from catalog.recommendation import compute_for_user
from common.models import SiteConfig
from journal.models import Mark, ShelfMember, ShelfType, TagMember
from users.models import User


@pytest.fixture(autouse=True)
def _isolate_site_config(_load_site_config):
    saved = SiteConfig.system
    SiteConfig.system = saved.model_copy()
    yield
    SiteConfig.system = saved


def _set(**kwargs):
    for k, v in kwargs.items():
        setattr(SiteConfig.system, k, v)


def _mark(identity, item) -> None:
    Mark(identity, item).update(ShelfType.COMPLETE, "", 0, [], 0)


@pytest.mark.django_db(databases="__all__")
class TestEvaluate:
    @pytest.fixture(autouse=True)
    def setup(self):
        _set(
            enable_recommendations=True,
            reco_min_source_marks=2,
            reco_min_target_marks=2,
            reco_similarity_top_k=10,
            reco_user_top_n=20,
            reco_user_idf_dampen=False,
            reco_similarity_shrinkage=0.0,
            reco_negative_weight=0.0,
            reco_seed_half_life_days=0,
            reco_per_seed_slots=0,
        )
        self.now = timezone.now()
        self.cutoff = self.now - timedelta(days=30)
        self.others = [
            User.register(email=f"ev{i}@t.com", username=f"ev_user{i}").identity
            for i in range(4)
        ]
        self.user = User.register(email="evt@t.com", username="ev_target")
        self.target = self.user.identity
        self.a, self.b, self.c, self.d, self.e = (
            Edition.objects.create(title=f"Eval {t}") for t in "ABCDE"
        )

    def _before_cutoff(self, identity, *items) -> None:
        for item in items:
            _mark(identity, item)
        ShelfMember._base_manager.filter(owner=identity, item__in=items).update(
            edited_time=self.now - timedelta(days=40)
        )

    def _co_marked_world(self) -> None:
        """Others marked A and B before T; the target marked A before, B after."""
        for ident in self.others:
            self._before_cutoff(ident, self.a, self.b)
        self._before_cutoff(self.target, self.a)
        _mark(self.target, self.b)

    def _evaluate(self, **kwargs):
        return evaluate(self.cutoff, users=0, min_seeds=1, **kwargs)

    def test_held_out_neighbour_is_a_hit(self):
        self._co_marked_world()
        result = self._evaluate()
        assert result.members_eligible == 1
        assert result.members_sampled == 1
        assert result.members_evaluated == 1
        assert result.ks == [10, 20]
        reco = result.recommendations
        at10 = reco.at_k[0]
        assert at10.k == 10
        assert at10.hit_rate == 1.0
        assert at10.recall == 1.0
        assert at10.precision == pytest.approx(0.1)
        assert reco.members_with_list == 1
        assert reco.coverage == 1.0
        assert reco.distinct_items == 1
        assert reco.mean_list_length == 1.0
        assert reco.by_category["book"].hits == 1
        assert reco.diversity.lead_seeds == 1
        assert reco.diversity.top_seed_share == 1.0
        assert reco.diversity.duplicate_rate == 0.0
        assert reco.diversity.intra_list_similarity == 0.0
        assert result.popularity.diversity.lead_seeds is None
        bucket = reco.by_bucket["<10"]
        assert (bucket.members, bucket.hit_rate, bucket.recall) == (1, 1.0, 1.0)
        assert set(reco.by_bucket) == {"<10"}

    def test_diversity_of_one_list(self):
        work = Work.objects.create(title="Eval work")
        work.editions.add(self.a, self.b)
        ItemCredit.objects.create(item=self.a, role="author", name="Same")
        ItemCredit.objects.create(item=self.c, role="author", name=" same")
        ItemCredit.objects.create(item=self.b, role="publisher", name="Same")
        a, b, c, d = self.a.pk, self.b.pk, self.c.pk, self.d.pk

        def similarity(sources):
            return [(a, c, 0.6), (c, a, 0.3), (a, d, 0.9)]

        lookups = DiversityLookups({a, b, c}, similarity)
        assert lookups.measure([a, b, c], {a: [d], b: [d], c: [a]}) == {
            "lead_seeds": 2,
            "top_seed_share": pytest.approx(2 / 3),
            "duplicates": 1,
            "rows": 3,
            "max_per_creator": 2,
            "intra_list_similarity": pytest.approx(0.2),
        }

    def test_hidden_category_truth_is_not_counted(self):
        self._co_marked_world()
        pref = self.user.preference
        pref.hidden_categories = ["book"]
        pref.save()
        result = self._evaluate()
        # the only held-out item is in a category the member hides, so
        # neither list can hit it and the member is left out
        assert result.members_sampled == 1
        assert result.members_evaluated == 0
        assert result.popularity.members_with_list == 0

    def test_too_few_seeds_is_not_eligible(self):
        self._co_marked_world()
        result = evaluate(self.cutoff, users=0, min_seeds=2)
        assert result.members_eligible == 0
        assert result.members_evaluated == 0

    def test_held_out_item_is_not_treated_as_shelved(self):
        self._co_marked_world()
        similarity = BuildItemSimilarity(until=self.cutoff).build_in_memory()
        replayed = compute_for_user(
            self.user.pk, self.target.pk, until=self.cutoff, similarity=similarity
        )
        assert [r.item_id for r in replayed] == [self.b.pk]
        current = compute_for_user(self.user.pk, self.target.pk, similarity=similarity)
        assert current == []

    def test_marks_after_cutoff_do_not_train(self):
        for ident in self.others:
            self._before_cutoff(ident, self.a)
            _mark(ident, self.c)
        self._before_cutoff(self.target, self.a)
        _mark(self.target, self.c)
        unbounded = BuildItemSimilarity().build_in_memory()
        assert (self.a.pk, self.c.pk) in {(s, t) for s, t, _ in unbounded([self.a.pk])}
        bounded = BuildItemSimilarity(until=self.cutoff).build_in_memory()
        assert not list(bounded([self.a.pk]))
        result = self._evaluate()
        # everyone marked A before and C after the cutoff
        assert result.members_evaluated == 5
        assert result.recommendations.at_k[0].hit_rate == 0.0

    def test_tags_after_cutoff_do_not_train(self):
        _set(reco_tag_weight=1.0)
        for ident in self.others[:2]:
            ident.tag_manager.tag_item(self.d, ["space"], 0)
            ident.tag_manager.tag_item(self.e, ["space"], 0)
        assert list(BuildItemSimilarity().build_in_memory()([self.d.pk]))
        bounded = BuildItemSimilarity(until=self.cutoff)
        assert not list(bounded.build_in_memory()([self.d.pk]))
        TagMember.objects.update(created_time=self.now - timedelta(days=40))
        assert list(bounded.build_in_memory()([self.d.pk]))

    def test_bounded_build_refuses_to_store(self):
        with pytest.raises(ValueError):
            BuildItemSimilarity(until=self.cutoff).run()

    def test_popularity_baseline(self):
        self._co_marked_world()
        pop = self._evaluate().popularity
        # A is the target's own seed, so B leads the list
        assert pop.at_k[0].hit_rate == 1.0
        assert pop.members_with_list == 1
        assert pop.distinct_items == 1
        assert pop.by_category["book"].hit_rate == 1.0

    def test_override_applies_and_is_restored(self):
        self._co_marked_world()
        result = self._evaluate(overrides={"reco_user_top_n": 5})
        assert result.top_n == 5
        assert result.ks == [5]
        assert result.settings["reco_user_top_n"] == 5
        assert result.overrides == {"reco_user_top_n": 5}
        assert SiteConfig.system.reco_user_top_n == 20
        # the build runs under the overrides too
        starved = self._evaluate(overrides={"reco_min_source_marks": 10})
        assert starved.recommendations.at_k[0].hit_rate == 0.0
        assert SiteConfig.system.reco_min_source_marks == 2

    def test_top_n_conflicting_with_override_is_refused(self):
        with pytest.raises(ValueError):
            self._evaluate(top_n=10, overrides={"reco_user_top_n": 5})

    def test_command_writes_nothing(self, tmp_path):
        self._co_marked_world()
        ItemSimilarity.objects.create(
            source=self.d,
            target=self.e,
            score=0.5,
            method=ItemSimilarity.METHOD_BLENDED,
        )
        UserRecommendation.objects.create(
            user=self.user, item=self.e, score=1.0, category="book"
        )
        before = (ItemSimilarity.objects.count(), UserRecommendation.objects.count())
        out = StringIO()
        path = tmp_path / "eval.json"
        call_command(
            "recommendation",
            "evaluate",
            "--users=0",
            "--min-seeds=1",
            "--set",
            "reco_user_top_n=15",
            f"--json={path}",
            stdout=out,
        )
        assert (
            ItemSimilarity.objects.count(),
            UserRecommendation.objects.count(),
        ) == before
        assert ItemSimilarity.objects.filter(source=self.d, target=self.e).exists()
        text = out.getvalue()
        assert "hit rate@10" in text
        assert "duplicate rate" in text
        assert "reco_user_top_n = 15  (override)" in text
        data = json.loads(path.read_text())
        assert data["top_n"] == 15
        assert data["members_evaluated"] == 1
        assert data["recommendations"]["at_k"][0]["hit_rate"] == 1.0
        assert "hit_rate" in data["popularity"]["at_k"][0]
        assert data["recommendations"]["diversity"]["lead_seeds"] == 1
        assert data["recommendations"]["by_bucket"]["<10"]["members"] == 1
        assert SiteConfig.system.reco_user_top_n == 20


def test_parse_overrides():
    assert parse_overrides(
        ["reco_user_idf_dampen=off", "reco_tag_weight=0.7", "reco_user_top_n=40"]
    ) == {"reco_user_idf_dampen": False, "reco_tag_weight": 0.7, "reco_user_top_n": 40}
    for bad in ("site_name=x", "reco_user_top_n", "reco_user_top_n=many", "reco_x=1"):
        with pytest.raises(ValueError):
            parse_overrides([bad])
