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
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from django.db.models import Count, Q

from catalog.jobs.recommendation import BuildItemSimilarity
from catalog.models import Item, Work
from catalog.recommendation import (
    SHELF_TYPES_AS_SEED,
    _categories_of,
    _live_items,
    compute_for_user,
    excluded_target_ctype_ids,
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
class ListMetrics:
    at_k: list[AtK]
    members_with_list: int
    coverage: float
    distinct_items: int
    mean_list_length: float
    by_category: dict[str, CategoryHits]


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


class _Tally:
    """Accumulates one list's hits over the evaluated members."""

    def __init__(self, ks: list[int]) -> None:
        self.ks = ks
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
    ) -> None:
        """``truth`` maps each id that counts as a hit to its held-out item."""
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

    def result(self) -> ListMetrics:
        n = self.members or 1
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
        )


def eligible_members(cutoff: datetime, min_seeds: int) -> list[tuple[int, int]]:
    """(identity, user) of local members with seeds before and marks after T."""
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
        .values_list("owner_id", "owner__user_id")
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
    it can be recommended at all: live, of a class that is a target, and not
    excluded for the member before T. A recommended sibling edition of a
    held-out item is a hit for that item. Members with nothing left to hit
    are not evaluated.
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
        raw_truth = _held_out([i for i, _ in sample], cutoff)
        mapped = {
            ident: {rewrite.get(i, i) for i in ids} for ident, ids in raw_truth.items()
        }
        live = _live_target_ids(set().union(*mapped.values()))
        siblings = _siblings_by_edition(live)
        popular = popular_items(cutoff, n + POPULARITY_SPARE)
        popular_cats = _categories_of(popular)
        timings["truth_and_baseline"] = time.monotonic() - started

        started = time.monotonic()
        reco = _Tally(ks)
        baseline = _Tally(ks)
        for identity_pk, user_pk in sample:
            _, excluded = user_reco_exclusions(user_pk, identity_pk, until=cutoff)
            held_out = (mapped.get(identity_pk, set()) & live) - excluded
            if not held_out:
                continue
            truth = {i: i for i in held_out}
            for i in held_out:
                for s in siblings.get(i, ()):
                    truth.setdefault(s, i)
            rows = compute_for_user(
                user_pk, identity_pk, until=cutoff, similarity=similarity
            )
            reco.add(
                [r.item_id for r in rows],
                {r.item_id: r.category for r in rows},
                truth,
                len(held_out),
            )
            baseline.add(
                [i for i in popular if i not in excluded][:n],
                popular_cats,
                truth,
                len(held_out),
            )
        timings["members"] = time.monotonic() - started

    return EvaluationResult(
        cutoff=cutoff,
        top_n=n,
        ks=ks,
        settings=settings,
        overrides=applied,
        members_eligible=len(eligible),
        members_sampled=len(sample),
        members_evaluated=reco.members,
        recommendations=reco.result(),
        popularity=baseline.result(),
        timings=timings,
    )
