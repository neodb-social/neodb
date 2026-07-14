"""Serving-time helpers for item and user recommendations.

Three surfaces, all visibility- and pref-gated by Preference.show_recommendations:
- similar_items(item, viewer): item-page "you might also like"
- recommendations_for(viewer): personalised, merging cached + circles
- from_your_circles(viewer): recent shelves from followees
"""

from collections import defaultdict
from datetime import timedelta
from heapq import nlargest

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, QuerySet
from django.utils import timezone
from loguru import logger

from common.models import SiteConfig
from journal.models import ShelfMember, q_piece_visible_to_user
from takahe.models import Identity as TakaheIdentity

from .models import (
    Item,
    ItemSimilarity,
    PerformanceProduction,
    PodcastEpisode,
    TVEpisode,
    TVShow,
    UserRecommendation,
    Work,
    item_content_types,
)

# Item classes that should never appear as recommendation targets.
# - TVShow: container; users typically mark TVSeasons
# - TVEpisode: too granular vs the season
# - PerformanceProduction: container under Performance
# - PodcastEpisode: too granular vs the podcast
EXCLUDED_RECO_TARGET_CLASSES: tuple[type[Item], ...] = (
    TVShow,
    TVEpisode,
    PerformanceProduction,
    PodcastEpisode,
)


def excluded_target_ctype_ids() -> set[int]:
    """ContentType ids for classes that must not be recommendation targets."""
    cts = item_content_types()
    return {cts[cls] for cls in EXCLUDED_RECO_TARGET_CLASSES if cls in cts}


def production_to_performance_map() -> dict[int, int]:
    """item_id of PerformanceProduction -> item_id of parent Performance.

    Marks on a Production are aggregated to its parent Performance so the
    aggregated signal reflects what users actually engaged with (the show),
    not a specific staging. Productions with no parent are not in the map.
    """
    return dict(
        PerformanceProduction.objects.filter(show_id__isnull=False).values_list(
            "pk", "show_id"
        )
    )


_PROD_TO_PERF_CACHE_KEY = "reco:prod_to_perf"
_PROD_TO_PERF_TTL = 3600


def production_to_performance_cached() -> dict[int, int]:
    cached = cache.get(_PROD_TO_PERF_CACHE_KEY)
    if cached is not None:
        return cached
    m = production_to_performance_map()
    cache.set(_PROD_TO_PERF_CACHE_KEY, m, timeout=_PROD_TO_PERF_TTL)
    return m


_LAZY_LOCK_TTL = 120  # seconds — covers typical compute duration

# Shelves that count as positive interest signal for recommendation training
# and as seeds for personalised recommendations. Wishlist is an explicit
# forward-looking taste signal; dropped is excluded (negative signal).
SHELF_TYPES_AS_SEED = ("wishlist", "progress", "complete")

# Shelves that mean the user has already engaged with an item, so we should
# never recommend it back to them. Includes "dropped" so we don't re-surface
# things they actively disliked.
SHELF_TYPES_TO_EXCLUDE = ("wishlist", "progress", "complete", "dropped")

# Surfaces that can be shown to anonymous viewers (the rest require a User).
ANON_VISIBLE_KINDS = frozenset({"similar_items"})


def can_show_reco(user, kind: str) -> bool:
    """Single visibility gate used by both HTML views and the Ninja API.

    - Authenticated user with a Preference row: defer to
      Preference.show_recommendations (master switch AND user has not
      opted out).
    - Authenticated user without a Preference row: conservatively False.
    - Anonymous viewer: only non-personalised surfaces (similar_items),
      gated by the site master switch.
    """
    if user and getattr(user, "is_authenticated", False):
        pref = getattr(user, "preference", None)
        return bool(pref and pref.show_recommendations(kind))
    if kind not in ANON_VISIBLE_KINDS:
        return False
    return bool(SiteConfig.system.enable_recommendations)


def _live_items(qs):
    """Filter to items that are valid recommendation *targets*.

    Drops soft-deleted/merged rows and the four classes that should never be
    recommended (TVShow / TVEpisode / PerformanceProduction / PodcastEpisode).
    """
    excluded = excluded_target_ctype_ids()
    qs = qs.filter(is_deleted=False, merged_to_item_id__isnull=True)
    if excluded:
        qs = qs.exclude(polymorphic_ctype_id__in=excluded)
    return qs


def _user_shelved_members(identity_pk: int) -> QuerySet[ShelfMember]:
    # _base_manager skips ShelfMemberManager's default annotations (useless
    # here); order_by() keeps subqueries free of any future default ordering.
    return ShelfMember._base_manager.filter(
        owner_id=identity_pk,
        parent__shelf_type__in=SHELF_TYPES_TO_EXCLUDE,
    ).order_by()


def _user_shelved_item_ids(identity_pk: int) -> set[int]:
    return set(_user_shelved_members(identity_pk).values_list("item_id", flat=True))


def _sibling_edition_ids(item_ids: set[int]) -> set[int]:
    """Edition ids sharing a Work with any Edition in ``item_ids``.

    Non-edition ids don't match the Work.editions through table, so the whole
    shelved set can be passed in. Single query via a work-id subquery.
    """
    if not item_ids:
        return set()
    through = Work.editions.through
    work_ids = through.objects.filter(edition_id__in=item_ids).values("work_id")
    return set(
        through.objects.filter(work_id__in=work_ids).values_list(
            "edition_id", flat=True
        )
    )


def similar_items(item: Item, viewer=None, limit: int = 10) -> list[Item]:
    """Return up to ``limit`` items similar to ``item``.

    Excludes items the viewer has already shelved (any state). Drops deleted
    and merged items. No author/owner visibility filter needed: ItemSimilarity
    is built from public marks only.
    """
    rows = list(
        ItemSimilarity.objects.filter(source=item)
        .order_by("-score")
        .values_list("target_id", flat=True)[: limit * 2]
    )
    if not rows:
        return []
    exclude: set[int] = set()
    if viewer and viewer.is_authenticated and getattr(viewer, "identity", None):
        exclude = _user_shelved_item_ids(viewer.identity.pk)
    qs = _live_items(Item.objects.filter(pk__in=rows))
    by_id = {i.pk: i for i in qs}
    out: list[Item] = []
    for iid in rows:
        if iid in exclude:
            continue
        i = by_id.get(iid)
        if i is None:
            continue
        out.append(i)
        if len(out) >= limit:
            break
    return out


def compute_for_user(user_pk: int, identity_pk: int) -> list[UserRecommendation]:
    """Score candidate items for one user, returning unsaved UserRecommendation rows."""
    sys = SiteConfig.system
    seed_cap = sys.reco_per_user_seed_cap
    top_n = sys.reco_user_top_n

    raw_seeds = list(
        ShelfMember.objects.filter(
            owner_id=identity_pk,
            visibility=0,
            parent__shelf_type__in=SHELF_TYPES_AS_SEED,
        )
        .order_by("-edited_time")
        .values_list("item_id", flat=True)[: seed_cap * 2]
    )
    if not raw_seeds:
        return []
    # Rewrite Production marks to their parent Performance so the user's
    # signal aggregates the same way it does in the similarity matrix.
    rewrite = production_to_performance_cached()
    seen: set[int] = set()
    seeds: list[int] = []
    for sid in raw_seeds:
        mapped = rewrite.get(sid, sid)
        if mapped in seen:
            continue
        seen.add(mapped)
        seeds.append(mapped)
        if len(seeds) >= seed_cap:
            break
    # Exclude shelved items plus their sibling editions (same Work). Precompute
    # only; a sibling marked later may dupe until the next refresh.
    shelved = _user_shelved_item_ids(identity_pk)
    excluded = shelved | _sibling_edition_ids(shelved)
    seed_set = set(seeds)

    scores: dict[int, float] = defaultdict(float)
    seeds_by_target: dict[int, list[int]] = defaultdict(list)
    sim_rows = ItemSimilarity.objects.filter(source_id__in=seeds).values_list(
        "source_id", "target_id", "score"
    )
    for src, tgt, score in sim_rows:
        if tgt in excluded or tgt in seed_set:
            continue
        scores[tgt] += score
        if len(seeds_by_target[tgt]) < 3:
            seeds_by_target[tgt].append(src)

    if not scores:
        return []

    top = nlargest(top_n, scores.items(), key=lambda t: t[1])
    if not top:
        return []
    target_ids = [t for t, _ in top]
    # category is a class attribute on each Item subclass, not a DB column,
    # so resolve via the polymorphic queryset and read the attribute.
    cats = {i.pk: str(i.category) for i in Item.objects.filter(pk__in=target_ids)}
    rows: list[UserRecommendation] = []
    for tgt, score in top:
        cat = cats.get(tgt)
        if not cat:
            continue
        rows.append(
            UserRecommendation(
                user_id=user_pk,
                item_id=tgt,
                score=score,
                seed_item_ids=seeds_by_target.get(tgt, []),
                category=cat,
            )
        )
    return rows


def _refresh_lazy(user_pk: int, identity_pk: int) -> bool:
    """Lazy on-demand recompute for one user.

    Guarded by a cache-based lock so concurrent requests for the same user
    don't dogpile compute + write. Returns True if this caller did the work,
    False if another request already holds the lock (skip-and-serve-stale).
    """
    lock_key = f"reco:lazy_refresh:{user_pk}"
    if not cache.add(lock_key, "1", timeout=_LAZY_LOCK_TTL):
        return False
    try:
        rows = compute_for_user(user_pk, identity_pk)
        with transaction.atomic():
            UserRecommendation.objects.filter(user_id=user_pk).delete()
            if rows:
                UserRecommendation.objects.bulk_create(rows, ignore_conflicts=True)
        return True
    finally:
        cache.delete(lock_key)


def _cached_user_rows(user_pk: int, ttl_days: int) -> list[UserRecommendation]:
    qs = UserRecommendation.objects.filter(user_id=user_pk).order_by("-score")
    rows = list(qs)
    if not rows:
        return []
    horizon = timezone.now() - timedelta(days=ttl_days)
    if rows[0].computed_at < horizon:
        return []
    return rows


def for_you(viewer, category: str | None = None, limit: int = 30) -> list[Item]:
    """Return personalised recommendations for the viewer."""
    if not viewer or not viewer.is_authenticated:
        return []
    identity = getattr(viewer, "identity", None)
    if identity is None:
        return []
    sys = SiteConfig.system
    rows = _cached_user_rows(viewer.pk, sys.reco_lazy_ttl_days)
    if not rows:
        try:
            _refresh_lazy(viewer.pk, identity.pk)
        except Exception as e:
            logger.exception(f"Lazy reco refresh failed for user {viewer.pk}: {e}")
            return []
        rows = _cached_user_rows(viewer.pk, sys.reco_lazy_ttl_days)
    if category:
        rows = [r for r in rows if r.category == category]
    target_ids = [r.item_id for r in rows[:limit]]
    if not target_ids:
        return []
    shelved = _user_shelved_item_ids(identity.pk)
    qs = _live_items(Item.objects.filter(pk__in=target_ids))
    by_id = {i.pk: i for i in qs}
    out: list[Item] = []
    for tid in target_ids:
        if tid in shelved:
            continue
        i = by_id.get(tid)
        if i:
            out.append(i)
    return out


def from_your_circles(
    viewer, category: str | None = None, limit: int = 30
) -> list[Item]:
    """Items recently marked by people the viewer follows, ranked by distinct shelvers.

    Per-request query (no precompute). Respects visibility via
    ``q_piece_visible_to_user`` and excludes items the viewer has already
    shelved.
    """
    if not viewer or not viewer.is_authenticated:
        return []
    identity = getattr(viewer, "identity", None)
    if identity is None:
        return []
    sys = SiteConfig.system
    since = timezone.now() - timedelta(days=sys.reco_circles_window_days)
    following = list(identity.following)
    if not following:
        return []
    not_discoverable = set(
        TakaheIdentity.objects.filter(pk__in=following, discoverable=False).values_list(
            "pk", flat=True
        )
    )
    eligible_followees = [f for f in following if f not in not_discoverable]
    if not eligible_followees:
        return []
    # Exclude via subquery: a materialized id set would inline one bind
    # parameter per shelved item, bloating the SQL for heavy users.
    shelved = _user_shelved_members(identity.pk).values("item_id")
    excluded_ctypes = excluded_target_ctype_ids()
    qs = (
        ShelfMember.objects.filter(q_piece_visible_to_user(viewer))
        .filter(
            owner_id__in=eligible_followees,
            edited_time__gte=since,
            parent__shelf_type__in=SHELF_TYPES_AS_SEED,
        )
        .exclude(item_id__in=shelved)
    )
    if excluded_ctypes:
        qs = qs.exclude(item__polymorphic_ctype_id__in=excluded_ctypes)
    rows = list(
        qs.values("item_id")
        .annotate(c=Count("owner_id", distinct=True))
        .order_by("-c")
        .values_list("item_id", "c")[: limit * 2]
    )
    target_ids = [iid for iid, _ in rows]
    if not target_ids:
        return []
    items_qs = _live_items(Item.objects.filter(pk__in=target_ids))
    by_id = {i.pk: i for i in items_qs}
    if category:
        by_id = {pk: i for pk, i in by_id.items() if str(i.category) == category}
    out: list[Item] = []
    for iid in target_ids:
        i = by_id.get(iid)
        if i:
            out.append(i)
        if len(out) >= limit:
            break
    return out


def blended_for_discover(viewer, limit: int = 30) -> list[Item]:
    """Mix personalised and circles results for the discover top row.

    Strategy: interleave by rank; dedup by item id; exclude items the viewer
    has already shelved; preserve order of first appearance. Returned list
    may be empty if neither source has data.
    """
    pref = (
        getattr(viewer, "preference", None)
        if viewer and viewer.is_authenticated
        else None
    )
    if pref is None:
        return []
    show_for_you = pref.show_recommendations("for_you")
    show_circles = pref.show_recommendations("from_circles")
    if not show_for_you and not show_circles:
        return []
    a = for_you(viewer, limit=limit) if show_for_you else []
    b = from_your_circles(viewer, limit=limit) if show_circles else []
    seen: set[int] = set()
    out: list[Item] = []
    a_iter = iter(a)
    b_iter = iter(b)
    while len(out) < limit:
        progressed = False
        for it in (next(a_iter, None), next(b_iter, None)):
            if it is None:
                continue
            progressed = True
            if it.pk in seen:
                continue
            seen.add(it.pk)
            out.append(it)
            if len(out) >= limit:
                break
        if not progressed:
            break
    return out
