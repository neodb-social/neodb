"""Serving-time helpers for item and user recommendations.

Three surfaces, all visibility- and pref-gated by Preference.show_recommendations:
- similar_items(item, viewer): item-page "you might also like"
- recommendations_for(viewer): personalised, merging cached + circles
- from_your_circles(viewer): recent shelves from followees
"""

import logging
from collections.abc import Collection, Iterable, Iterator
from datetime import timedelta
from heapq import heappush, heapreplace, nlargest

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, QuerySet
from django.utils import timezone

from common.models import SiteConfig
from journal.models import (
    Comment,
    Content,
    Note,
    Rating,
    Review,
    ShelfMember,
    q_piece_visible_to_user,
)
from takahe.models import Identity as TakaheIdentity
from users.models import APIdentity, User

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

logger = logging.getLogger(__name__)

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


# same bound as Item.final_item
_MERGE_CHAIN_MAX_HOPS = 20


def _compose_rewrites(
    merged: dict[int, int], productions: dict[int, int], deleted: set[int]
) -> dict[int, int]:
    """Combine merge edges and Production -> Performance edges into one map.

    A key follows its merge chain, then its Production's Performance, then
    that Performance's merge chain. Chains that loop, exceed the hop bound
    or end on a deleted item are left out. No value is also a key.
    """

    def final(i: int) -> int | None:
        hops = 0
        while i in merged:
            if hops >= _MERGE_CHAIN_MAX_HOPS:
                return None
            i = merged[i]
            hops += 1
        return None if hops and i in deleted else i

    out: dict[int, int] = {}
    for key in merged.keys() | productions.keys():
        resolved = final(key)
        if resolved is not None and resolved in productions:
            resolved = final(productions[resolved])
        if resolved is not None and resolved != key:
            out[key] = resolved
    return out


def training_rewrite_map() -> dict[int, int]:
    """item_id -> item_id whose marks it should count toward.

    Covers merged items (to their live final item) and Productions (to their
    Performance, itself resolved through merges).
    """
    merged_qs = Item.objects.filter(merged_to_item_id__isnull=False)
    merged = dict(merged_qs.values_list("pk", "merged_to_item_id"))
    # every chain end after at least one hop is some item's merge target
    deleted = set(
        Item.objects.filter(
            is_deleted=True, pk__in=merged_qs.values("merged_to_item_id")
        ).values_list("pk", flat=True)
    )
    return _compose_rewrites(merged, production_to_performance_map(), deleted)


def _scoped_rewrite_map(start: QuerySet) -> dict[int, int]:
    """``training_rewrite_map`` restricted to chains reachable from ``start``.

    ``start`` is a ``values("item_id")``-style subquery, so a heavy user's ids
    are never inlined. Costs two small queries per hop, usually two hops.
    """
    merged: dict[int, int] = {}
    productions: dict[int, int] = {}
    seen: set[int] = set()
    frontier: QuerySet | set[int] = start
    # merge chain, Production hop, then the Performance's merge chain
    for _ in range(2 * _MERGE_CHAIN_MAX_HOPS + 2):
        m = dict(
            Item.objects.filter(
                pk__in=frontier, merged_to_item_id__isnull=False
            ).values_list("pk", "merged_to_item_id")
        )
        p = dict(
            PerformanceProduction.objects.filter(
                pk__in=frontier, show_id__isnull=False
            ).values_list("pk", "show_id")
        )
        merged.update(m)
        productions.update(p)
        nxt = (set(m.values()) | set(p.values())) - seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    if not merged and not productions:
        return {}
    deleted = set(
        Item.objects.filter(pk__in=seen, is_deleted=True).values_list("pk", flat=True)
    )
    return _compose_rewrites(merged, productions, deleted)


def _with_rewrites(item_ids: set[int], rewrite: dict[int, int]) -> set[int]:
    return item_ids | {rewrite[i] for i in item_ids if i in rewrite}


MIN_GRADES_FOR_MEAN = 3
MIN_RATING_FACTOR = 0.2
MAX_MARK_WEIGHT = 2.0
# a target's score sums only its strongest seed contributions, so many weak
# links cannot outrank a few strong ones
SEED_CONTRIB_CAP = 5


def mark_weight(
    grade: int | None,
    user_mean: float | None,
    has_review: bool,
    has_comment: bool,
    has_note: bool,
    rating_weight: float,
    review_boost: float,
    comment_note_boost: float,
) -> float:
    """How strongly one mark signals interest, in [0.2, 2.0], neutral 1.0.

    The rating is centered on the owner's own mean, so a harsh rater's 7 and
    a generous rater's 9 can mean the same; 4.5 is half the 1..10 range.
    """
    rating_factor = 1.0
    if grade and user_mean is not None:
        rating_factor = max(
            MIN_RATING_FACTOR, 1.0 + rating_weight * (grade - user_mean) / 4.5
        )
    presence = (
        1.0
        + review_boost * has_review
        + comment_note_boost * (int(has_comment) + int(has_note))
    )
    return min(MAX_MARK_WEIGHT, rating_factor * presence)


def user_mean_grade(grades: Iterable[int | None]) -> float | None:
    """Mean of the given grades, or None with too few to trust."""
    graded = [g for g in grades if g]
    if len(graded) < MIN_GRADES_FOR_MEAN:
        return None
    return sum(graded) / len(graded)


# (grade, has_comment, has_review, has_note) for one owner and item
MarkSignals = tuple[int | None, bool, bool, bool]
NO_MARK_SIGNALS: MarkSignals = (None, False, False, False)


def load_mark_signals(
    wanted: dict[int, set[int]],
    rewrite: dict[int, int],
    item_ids: Collection[int] | None = None,
) -> dict[tuple[int, int], MarkSignals]:
    """Public rating, comment, review and note for each wanted (owner, item).

    ``wanted`` maps owner id to item ids after rewrite; content on a rewrite
    source counts for its target. Loaded per owner instead of per mark,
    because Comment and Review have no (owner, item) index. ``item_ids``
    optionally narrows the scan to these raw item ids. Pairs without any
    signal are absent; use ``NO_MARK_SIGNALS``.
    """
    owners = list(wanted)

    def public_rows(model: type[Content], *fields: str) -> Iterator[tuple]:
        qs = model.objects.filter(owner_id__in=owners, visibility=0)
        if item_ids is not None:
            qs = qs.filter(item_id__in=item_ids)
        return (
            qs.order_by()
            .values_list("owner_id", "item_id", *fields)
            .iterator(chunk_size=20_000)
        )

    grades: dict[tuple[int, int], int] = {}
    for owner_id, item_id, grade in public_rows(Rating, "grade"):
        target = rewrite.get(item_id, item_id)
        if not grade or target not in wanted[owner_id]:
            continue
        key = (owner_id, target)
        # a rating on the item itself wins over one on a merged duplicate
        if key not in grades or target == item_id:
            grades[key] = grade
    present: list[set[tuple[int, int]]] = []
    for model in (Comment, Review, Note):
        found: set[tuple[int, int]] = set()
        for owner_id, item_id in public_rows(model):
            target = rewrite.get(item_id, item_id)
            if target in wanted[owner_id]:
                found.add((owner_id, target))
        present.append(found)
    comments, reviews, notes = present
    return {
        key: (grades.get(key), key in comments, key in reviews, key in notes)
        for key in grades.keys() | comments | reviews | notes
    }


def reco_mark_weight(
    sys: SiteConfig.SystemOptions,
    grade: int | None,
    user_mean: float | None,
    *,
    has_comment: bool,
    has_review: bool,
    has_note: bool,
) -> float:
    """``mark_weight`` with the site's settings."""
    return mark_weight(
        grade,
        user_mean,
        has_review=has_review,
        has_comment=has_comment,
        has_note=has_note,
        rating_weight=sys.reco_rating_weight,
        review_boost=sys.reco_review_boost,
        comment_note_boost=sys.reco_comment_note_boost,
    )


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


def _user_rewrite_map(identity_pk: int) -> dict[int, int]:
    return _scoped_rewrite_map(_user_shelved_members(identity_pk).values("item_id"))


def _user_excluded_item_ids(identity_pk: int) -> set[int]:
    """Shelved items plus what they count toward in training.

    A user who shelved a merged edition or a Production should not be offered
    the surviving edition or the Performance.
    """
    return _with_rewrites(
        _user_shelved_item_ids(identity_pk), _user_rewrite_map(identity_pk)
    )


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
        ItemSimilarity.objects.filter(source=item, method=ItemSimilarity.METHOD_BLENDED)
        .order_by("-score")
        .values_list("target_id", flat=True)[: limit * 2]
    )
    if not rows:
        return []
    exclude: set[int] = set()
    if viewer and viewer.is_authenticated and getattr(viewer, "identity", None):
        exclude = _user_excluded_item_ids(viewer.identity.pk)
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
        ShelfMember._base_manager.filter(
            owner_id=identity_pk,
            visibility=0,
            parent__shelf_type__in=SHELF_TYPES_AS_SEED,
        )
        .order_by("-edited_time")
        .values_list("item_id", flat=True)[: seed_cap * 2]
    )
    if not raw_seeds:
        return []
    # Rewrite seeds the same way the similarity matrix aggregates marks.
    # Seeds are a subset of the shelved items the scoped map starts from.
    rewrite = _user_rewrite_map(identity_pk)
    seeds: list[int] = []
    seed_set: set[int] = set()
    for sid in raw_seeds:
        mapped = rewrite.get(sid, sid)
        if mapped in seed_set:
            continue
        seed_set.add(mapped)
        seeds.append(mapped)
        if len(seeds) >= seed_cap:
            break
    # content written on a rewrite target counts too, as it does in training
    signals = load_mark_signals(
        {identity_pk: seed_set},
        rewrite,
        item_ids=set(raw_seeds) | {rewrite[s] for s in raw_seeds if s in rewrite},
    )
    seed_signals = [signals.get((identity_pk, s), NO_MARK_SIGNALS) for s in seeds]
    user_mean = user_mean_grade(grade for grade, _, _, _ in seed_signals)
    seed_weight = {
        sid: reco_mark_weight(
            sys,
            grade,
            user_mean,
            has_comment=has_comment,
            has_review=has_review,
            has_note=has_note,
        )
        for sid, (grade, has_comment, has_review, has_note) in zip(seeds, seed_signals)
    }
    # Exclude shelved items plus their sibling editions (same Work). Precompute
    # only; a sibling marked later may dupe until the next refresh.
    shelved = _with_rewrites(_user_shelved_item_ids(identity_pk), rewrite)
    excluded = shelved | _sibling_edition_ids(shelved)

    # min-heap of the strongest (contribution, seed) pairs per target
    contribs: dict[int, list[tuple[float, int]]] = {}
    sim_rows = ItemSimilarity.objects.filter(
        source_id__in=seeds, method=ItemSimilarity.METHOD_BLENDED
    ).values_list("source_id", "target_id", "score")
    for src, tgt, score in sim_rows:
        if tgt in excluded or tgt in seed_set:
            continue
        entry = (seed_weight[src] * score, src)
        heap = contribs.setdefault(tgt, [])
        if len(heap) < SEED_CONTRIB_CAP:
            heappush(heap, entry)
        elif entry > heap[0]:
            heapreplace(heap, entry)

    if not contribs:
        return []
    scores = {tgt: sum(c for c, _ in heap) for tgt, heap in contribs.items()}
    seeds_by_target = {
        tgt: [src for _, src in nlargest(3, heap)] for tgt, heap in contribs.items()
    }

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
    shelved = _user_excluded_item_ids(identity.pk)
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


_CIRCLES_TTL = 3600


def _circles_ranked_ids(
    viewer: User, identity: APIdentity, following: list[int], limit: int
) -> list[int]:
    sys = SiteConfig.system
    since = timezone.now() - timedelta(days=sys.reco_circles_window_days)
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
    return list(
        qs.values("item_id")
        .annotate(c=Count("owner_id", distinct=True))
        .order_by("-c")
        .values_list("item_id", flat=True)[: limit * 2]
    )


def from_your_circles(
    viewer, category: str | None = None, limit: int = 30
) -> list[Item]:
    """Items recently marked by people the viewer follows, ranked by distinct shelvers.

    The ranked ids are cached per viewer for ``_CIRCLES_TTL``, so follow
    changes and new marks by followees show up late; items the viewer shelved
    since then are dropped on every read. Respects visibility via
    ``q_piece_visible_to_user``.
    """
    if not viewer or not viewer.is_authenticated:
        return []
    identity = getattr(viewer, "identity", None)
    if identity is None:
        return []
    # not cached, so a new member's first follows show up at once
    following = list(identity.following)
    if not following:
        return []
    key = f"reco:circles:{viewer.pk}:{limit}"
    target_ids = cache.get(key)
    if target_ids is None:
        target_ids = _circles_ranked_ids(viewer, identity, following, limit)
        cache.set(key, target_ids, timeout=_CIRCLES_TTL)
    if not target_ids:
        return []
    shelved = set(
        _user_shelved_members(identity.pk)
        .filter(item_id__in=target_ids)
        .values_list("item_id", flat=True)
    )
    items_qs = _live_items(
        Item.objects.filter(pk__in=[i for i in target_ids if i not in shelved])
    )
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
