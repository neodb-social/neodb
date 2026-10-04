"""Offline evaluation of the personalised recommendation settings.

Replays every sampled member as of a cutoff time T: the item similarity
and the member's seeds come only from data before T, and the seed-shelf
marks at or after T are the held-out truth. A popularity list over the
same members is the baseline. Nothing is written to the database.

Known approximations, all of them as of now rather than as of T:
- a ShelfMember is one row per member and item, so a mark moved to
  another shelf after T counts as held out and not as a seed;
- a rating is edited in place, so a grade changed after T is seen for a
  mark from before T;
- deletions, merges, credits, tag titles and visibility, follows and
  discoverability, catalog languages and hidden categories.
"""

import random
import time
from collections.abc import Hashable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.db.models import Count, Q

from catalog.jobs.recommendation import CONTENT_CREDIT_ROLES, BuildItemSimilarity
from catalog.models import Item, ItemCredit, Work
from catalog.recommendation import (
    SHELF_TYPES_AS_SEED,
    RecoGroups,
    SimilarityLookup,
    _categories_of,
    _group_of,
    _live_items,
    compute_for_user,
    excluded_target_ctype_ids,
    reco_groups,
    recommendable_for_user,
    training_rewrite_map,
    user_reco_exclusions,
)
from common.models import SiteConfig
from journal.models import ShelfMember

OverrideValue = int | float | bool

# window of public marks the popularity baseline counts, before T
POPULARITY_WINDOW_DAYS = 90
# popular items fetched beyond top_n, so the member's own shelved items can
# be skipped and the list still fill up
POPULARITY_SPARE = 1000
_ID_CHUNK = 1000
# lower bounds of the member buckets by seed-shelf marks before T
MARK_BUCKETS = ((0, "<10"), (10, "10-49"), (50, "50-199"), (200, "200+"))
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def parse_overrides(pairs: Sequence[str]) -> dict[str, OverrideValue]:
    """``key=value`` strings to typed ``reco_*`` setting values."""
    fields = SiteConfig.SystemOptions.model_fields
    out: dict[str, OverrideValue] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        key, raw = key.strip(), raw.strip()
        if not sep or not key.startswith("reco_") or key not in fields:
            raise ValueError(f"not a reco_* setting: {pair!r}")
        kind = fields[key].annotation
        try:
            if kind is bool:
                if raw.lower() not in _TRUE | _FALSE:
                    raise ValueError(raw)
                out[key] = raw.lower() in _TRUE
            elif kind is int:
                out[key] = int(raw)
            elif kind is float:
                out[key] = float(raw)
            else:
                raise ValueError(f"unsupported type {kind}")
        except ValueError as e:
            raise ValueError(f"bad value for {key}: {raw!r}") from e
    return out


@contextmanager
def site_overrides(overrides: Mapping[str, OverrideValue]) -> Iterator[None]:
    """Apply settings to this process only, on a copy swapped back afterwards."""
    saved = SiteConfig.system
    SiteConfig.system = saved.model_copy(update=dict(overrides))
    try:
        yield
    finally:
        SiteConfig.system = saved


@dataclass
class AtK:
    k: int
    hit_rate: float
    precision: float
    recall: float


@dataclass
class CategoryHits:
    # members with at least one row of the category in their list
    members: int
    hits: int
    hit_rate: float


@dataclass
class Diversity:
    # means over the members with a list; the seed ones only for lists
    # with seeds
    lead_seeds: float | None
    top_seed_share: float | None
    # rows sharing a Work or show with a row ranked above them, of all rows
    duplicate_rate: float
    # rows of the creator with the most rows in the list
    max_per_creator: float
    # blended similarity of a pair of rows, missing pairs as 0
    intra_list_similarity: float


@dataclass
class BucketMetrics:
    members: int
    hit_rate: float
    recall: float
    diversity: Diversity


@dataclass
class ListMetrics:
    at_k: list[AtK]
    members_with_list: int
    coverage: float
    distinct_items: int
    mean_list_length: float
    by_category: dict[str, CategoryHits]
    diversity: Diversity
    # keyed by seed-shelf marks before T, see MARK_BUCKETS
    by_bucket: dict[str, BucketMetrics]


@dataclass
class EvaluationResult:
    cutoff: datetime
    top_n: int
    ks: list[int]
    settings: dict[str, Any]
    overrides: dict[str, OverrideValue]
    members_eligible: int
    members_sampled: int
    members_evaluated: int
    recommendations: ListMetrics
    popularity: ListMetrics
    timings: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["cutoff"] = self.cutoff.isoformat()
        return out


def mark_bucket(marks: int) -> str:
    return [label for low, label in MARK_BUCKETS if marks >= low][-1]


class DiversityLookups:
    """Groups, creators and pair similarity of every listed item, loaded once."""

    def __init__(self, item_ids: set[int], similarity: SimilarityLookup) -> None:
        self.groups: RecoGroups = reco_groups(item_ids)
        self.creators: dict[int, set[Hashable]] = {}
        ids = list(item_ids)
        for start in range(0, len(ids), _ID_CHUNK * 5):
            rows = (
                ItemCredit.objects.filter(
                    item_id__in=ids[start : start + _ID_CHUNK * 5],
                    role__in=CONTENT_CREDIT_ROLES,
                )
                .order_by()
                .values_list("item_id", "person_id", "name")
            )
            for item_id, person_id, name in rows:
                if person_id is not None:
                    key: Hashable = person_id
                elif name and name.strip():
                    key = name.strip().casefold()
                else:
                    continue
                self.creators.setdefault(item_id, set()).add(key)
        self.similarity = similarity

    def measure(self, ranked: list[int], seeds: Mapping[int, list[int]]) -> dict:
        """One list's raw diversity values; seed ones are None without seeds."""
        leads = [seeds[i][0] for i in ranked if seeds.get(i)]
        lead_seeds = top_share = None
        if leads:
            per_lead: dict[int, int] = {}
            for lead in leads:
                per_lead[lead] = per_lead.get(lead, 0) + 1
            lead_seeds = len(per_lead)
            top_share = max(per_lead.values()) / len(ranked)
        seen: set[Hashable] = set()
        duplicates = 0
        for i in ranked:
            key = _group_of(i, self.groups)
            duplicates += key in seen
            seen.add(key)
        per_creator: dict[Hashable, int] = {}
        for i in ranked:
            for c in self.creators.get(i, ()):
                per_creator[c] = per_creator.get(c, 0) + 1
        listed = set(ranked)
        pairs: dict[tuple[int, int], float] = {}
        for src, tgt, score in self.similarity(ranked):
            if tgt in listed:
                key = (min(src, tgt), max(src, tgt))
                pairs[key] = max(pairs.get(key, 0.0), score)
        n_pairs = len(ranked) * (len(ranked) - 1) // 2
        return {
            "lead_seeds": lead_seeds,
            "top_seed_share": top_share,
            "duplicates": duplicates,
            "rows": len(ranked),
            "max_per_creator": max(per_creator.values(), default=0),
            "intra_list_similarity": sum(pairs.values()) / n_pairs if n_pairs else 0.0,
        }


def _diversity(measured: list[dict]) -> Diversity:
    def mean(key: str) -> float | None:
        values = [m[key] for m in measured if m[key] is not None]
        return sum(values) / len(values) if values else None

    rows = sum(m["rows"] for m in measured)
    return Diversity(
        lead_seeds=mean("lead_seeds"),
        top_seed_share=mean("top_seed_share"),
        duplicate_rate=sum(m["duplicates"] for m in measured) / rows if rows else 0.0,
        max_per_creator=mean("max_per_creator") or 0.0,
        intra_list_similarity=mean("intra_list_similarity") or 0.0,
    )


class _Tally:
    """Accumulates one list's hits over the evaluated members."""

    def __init__(self, ks: list[int]) -> None:
        self.ks = ks
        # (ranked, seeds, bucket, hit, recall) per member, over the whole list
        self.lists: list[
            tuple[list[int], Mapping[int, list[int]], str, bool, float]
        ] = []
        self.hit = dict.fromkeys(ks, 0)
        self.precision = dict.fromkeys(ks, 0.0)
        self.recall = dict.fromkeys(ks, 0.0)
        self.members = 0
        self.with_list = 0
        self.lengths = 0
        self.items: set[int] = set()
        self.cat_members: dict[str, int] = {}
        self.cat_hits: dict[str, int] = {}

    def add(
        self,
        ranked: list[int],
        cats: Mapping[int, str],
        truth: Mapping[int, int],
        held_out: int,
        bucket: str,
        seeds: Mapping[int, list[int]] | None = None,
    ) -> None:
        """``truth`` maps each id that counts as a hit to its held-out item."""
        hits = len({truth[i] for i in ranked if i in truth})
        self.lists.append(
            (ranked, seeds or {}, bucket, hits > 0, min(1.0, hits / held_out))
        )
        self.members += 1
        self.lengths += len(ranked)
        self.items.update(ranked)
        if ranked:
            self.with_list += 1
        for k in self.ks:
            hits = len({truth[i] for i in ranked[:k] if i in truth})
            self.hit[k] += hits > 0
            self.precision[k] += hits / k
            self.recall[k] += min(1.0, hits / held_out)
        shown: set[str] = set()
        hit_cats: set[str] = set()
        for i in ranked:
            cat = cats.get(i)
            if cat is None:
                continue
            shown.add(cat)
            if i in truth:
                hit_cats.add(cat)
        for cat in shown:
            self.cat_members[cat] = self.cat_members.get(cat, 0) + 1
            self.cat_hits[cat] = self.cat_hits.get(cat, 0) + (cat in hit_cats)

    def result(self, lookups: DiversityLookups) -> ListMetrics:
        n = self.members or 1
        measured: dict[str, list[dict]] = {}
        hit: dict[str, list[bool]] = {}
        recall: dict[str, list[float]] = {}
        for ranked, seeds, bucket, was_hit, rec in self.lists:
            hit.setdefault(bucket, []).append(was_hit)
            recall.setdefault(bucket, []).append(rec)
            if ranked:
                measured.setdefault(bucket, []).append(lookups.measure(ranked, seeds))
        return ListMetrics(
            at_k=[
                AtK(
                    k=k,
                    hit_rate=self.hit[k] / n,
                    precision=self.precision[k] / n,
                    recall=self.recall[k] / n,
                )
                for k in self.ks
            ],
            members_with_list=self.with_list,
            coverage=self.with_list / n,
            distinct_items=len(self.items),
            mean_list_length=self.lengths / n,
            by_category={
                cat: CategoryHits(
                    members=m,
                    hits=self.cat_hits[cat],
                    hit_rate=self.cat_hits[cat] / m,
                )
                for cat, m in sorted(self.cat_members.items())
            },
            diversity=_diversity([m for ms in measured.values() for m in ms]),
            by_bucket={
                label: BucketMetrics(
                    members=len(hit[label]),
                    hit_rate=sum(hit[label]) / len(hit[label]),
                    recall=sum(recall[label]) / len(recall[label]),
                    diversity=_diversity(measured.get(label, [])),
                )
                for _, label in MARK_BUCKETS
                if label in hit
            },
        )


def eligible_members(cutoff: datetime, min_seeds: int) -> list[tuple[int, int, int]]:
    """(identity, user, seed-shelf marks before T) of members with marks after T too."""
    rows = (
        ShelfMember._base_manager.filter(
            parent__shelf_type__in=SHELF_TYPES_AS_SEED,
            owner__user_id__isnull=False,
        )
        .order_by()
        .values("owner_id", "owner__user_id")
        .annotate(
            before=Count("id", filter=Q(edited_time__lt=cutoff)),
            after=Count("id", filter=Q(edited_time__gte=cutoff)),
        )
        .filter(before__gte=min_seeds, after__gte=1)
        .values_list("owner_id", "owner__user_id", "before")
    )
    return sorted(rows)


def _held_out(identity_ids: list[int], cutoff: datetime) -> dict[int, set[int]]:
    """Raw item ids of each member's seed-shelf marks at or after T."""
    out: dict[int, set[int]] = {}
    for start in range(0, len(identity_ids), _ID_CHUNK):
        rows = (
            ShelfMember._base_manager.filter(
                owner_id__in=identity_ids[start : start + _ID_CHUNK],
                parent__shelf_type__in=SHELF_TYPES_AS_SEED,
                edited_time__gte=cutoff,
            )
            .order_by()
            .values_list("owner_id", "item_id")
        )
        for owner_id, item_id in rows:
            out.setdefault(owner_id, set()).add(item_id)
    return out


def _live_target_ids(item_ids: set[int]) -> set[int]:
    ids = list(item_ids)
    out: set[int] = set()
    for start in range(0, len(ids), _ID_CHUNK * 5):
        out.update(
            _live_items(Item.objects.filter(pk__in=ids[start : start + _ID_CHUNK * 5]))
            .order_by()
            .values_list("pk", flat=True)
        )
    return out


def _siblings_by_edition(item_ids: set[int]) -> dict[int, set[int]]:
    """Each Edition in ``item_ids`` to the editions sharing a Work with it."""
    through = Work.editions.through
    ids = list(item_ids)
    works: dict[int, set[int]] = {}
    for start in range(0, len(ids), _ID_CHUNK * 5):
        for work_id, edition_id in through.objects.filter(
            edition_id__in=ids[start : start + _ID_CHUNK * 5]
        ).values_list("work_id", "edition_id"):
            works.setdefault(edition_id, set()).add(work_id)
    work_ids = list({w for ws in works.values() for w in ws})
    editions: dict[int, set[int]] = {}
    for start in range(0, len(work_ids), _ID_CHUNK * 5):
        for work_id, edition_id in through.objects.filter(
            work_id__in=work_ids[start : start + _ID_CHUNK * 5]
        ).values_list("work_id", "edition_id"):
            editions.setdefault(work_id, set()).add(edition_id)
    return {
        e: set().union(*(editions.get(w, set()) for w in ws)) - {e}
        for e, ws in works.items()
    }


def popular_items(cutoff: datetime, limit: int) -> list[int]:
    """Items by distinct owners of public seed-shelf marks in the window before T."""
    qs = ShelfMember._base_manager.filter(
        visibility=0,
        parent__shelf_type__in=SHELF_TYPES_AS_SEED,
        edited_time__gte=cutoff - timedelta(days=POPULARITY_WINDOW_DAYS),
        edited_time__lt=cutoff,
        item__is_deleted=False,
        item__merged_to_item_id__isnull=True,
    )
    excluded = excluded_target_ctype_ids()
    if excluded:
        qs = qs.exclude(item__polymorphic_ctype_id__in=excluded)
    return list(
        qs.order_by()
        .values("item_id")
        .annotate(n=Count("owner_id", distinct=True))
        .order_by("-n", "item_id")
        .values_list("item_id", flat=True)[:limit]
    )


def evaluate(
    cutoff: datetime,
    *,
    users: int = 200,
    sample_seed: int = 0,
    min_seeds: int = 5,
    top_n: int | None = None,
    overrides: Mapping[str, OverrideValue] | None = None,
) -> EvaluationResult:
    """Measure how well recommendations made as of ``cutoff`` predict later marks.

    ``users`` caps the random sample of eligible members, 0 for all of them.
    ``top_n`` overrides ``reco_user_top_n``. A held-out item counts only if
    it can be recommended at all: live, of a class that is a target, not
    excluded for the member before T, and allowed by the member's hidden
    categories and catalog languages. The popularity list is filtered the
    same way per member. A recommended sibling edition of a held-out item is
    a hit for that item. Members with nothing left to hit are not evaluated.
    """
    applied = dict(overrides or {})
    if top_n is not None:
        if applied.get("reco_user_top_n", top_n) != top_n:
            raise ValueError("top_n and the reco_user_top_n override disagree")
        applied["reco_user_top_n"] = top_n
    with site_overrides(applied):
        sys = SiteConfig.system
        settings = {
            k: getattr(sys, k)
            for k in SiteConfig.SystemOptions.model_fields
            if k.startswith("reco_")
        }
        n = sys.reco_user_top_n
        ks = sorted({k for k in (10, 30, n) if 0 < k <= n})
        timings: dict[str, float] = {}

        eligible = eligible_members(cutoff, min_seeds)
        sample = eligible
        if users and users < len(eligible):
            sample = sorted(random.Random(sample_seed).sample(eligible, users))

        started = time.monotonic()
        similarity = BuildItemSimilarity(until=cutoff).build_in_memory()
        timings["similarity_build"] = time.monotonic() - started

        started = time.monotonic()
        rewrite = training_rewrite_map()
        raw_truth = _held_out([i for i, _, _ in sample], cutoff)
        mapped = {
            ident: {rewrite.get(i, i) for i in ids} for ident, ids in raw_truth.items()
        }
        live = _live_target_ids(set().union(*mapped.values()))
        siblings = _siblings_by_edition(live)
        popular = popular_items(cutoff, n + POPULARITY_SPARE)
        popular_cats = _categories_of(popular)
        # one category lookup for every id a member's filter may see
        cats = {
            **_categories_of(live | set().union(*siblings.values())),
            **popular_cats,
        }
        timings["truth_and_baseline"] = time.monotonic() - started

        started = time.monotonic()
        reco = _Tally(ks)
        baseline = _Tally(ks)
        for identity_pk, user_pk, marks in sample:
            _, excluded = user_reco_exclusions(user_pk, identity_pk, until=cutoff)
            held_out = (mapped.get(identity_pk, set()) & live) - excluded
            if not held_out:
                continue
            related = set().union(*(siblings.get(i, set()) for i in held_out))
            allowed = recommendable_for_user(
                user_pk, [*held_out, *related, *popular], cats
            )
            held_out &= allowed
            if not held_out:
                continue
            truth = {i: i for i in held_out}
            for i in held_out:
                for s in siblings.get(i, ()):
                    if s in allowed:
                        truth.setdefault(s, i)
            rows = compute_for_user(
                user_pk, identity_pk, until=cutoff, similarity=similarity
            )
            bucket = mark_bucket(marks)
            reco.add(
                [r.item_id for r in rows],
                {r.item_id: r.category for r in rows},
                truth,
                len(held_out),
                bucket,
                {r.item_id: r.seed_item_ids for r in rows},
            )
            baseline.add(
                [i for i in popular if i in allowed and i not in excluded][:n],
                popular_cats,
                truth,
                len(held_out),
                bucket,
            )
        timings["members"] = time.monotonic() - started

        started = time.monotonic()
        lookups = DiversityLookups(reco.items | baseline.items, similarity)
        reco_metrics = reco.result(lookups)
        popularity_metrics = baseline.result(lookups)
        timings["diversity"] = time.monotonic() - started

    return EvaluationResult(
        cutoff=cutoff,
        top_n=n,
        ks=ks,
        settings=settings,
        overrides=applied,
        members_eligible=len(eligible),
        members_sampled=len(sample),
        members_evaluated=reco.members,
        recommendations=reco_metrics,
        popularity=popularity_metrics,
        timings=timings,
    )
