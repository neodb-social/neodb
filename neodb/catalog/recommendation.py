"""Serving-time helpers for item and user recommendations.

Surfaces, all visibility- and pref-gated by Preference.show_recommendations:
- similar_items(item, viewer): item-page "you might also like"
- for_you(viewer): personalised, from the stored per-user rows
- from_your_circles(viewer): recent shelves from followees
- blended_for_discover(viewer): for_you and from_your_circles interleaved
"""

import logging
from collections.abc import (
    Callable,
    Collection,
    Hashable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from datetime import datetime, timedelta
from functools import partial
from heapq import nlargest
from typing import NamedTuple

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Max, Q, QuerySet
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
from takahe.utils import Takahe
from users.models import APIdentity, Preference, User

from .models import (
    Edition,
    Item,
    ItemSimilarity,
    PerformanceProduction,
    PodcastEpisode,
    RecommendationDismissal,
    TVEpisode,
    TVSeason,
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
        return None if i in deleted else i

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
    productions_qs = PerformanceProduction.objects.filter(show_id__isnull=False)
    # a chain can only end on a merge target or a Production's Performance
    deleted = set(
        Item.objects.filter(is_deleted=True)
        .filter(
            Q(pk__in=merged_qs.values("merged_to_item_id"))
            | Q(pk__in=productions_qs.values("show_id"))
        )
        .values_list("pk", flat=True)
    )
    productions = dict(productions_qs.values_list("pk", "show_id"))
    return _compose_rewrites(merged, productions, deleted)


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
    *,
    public_only: bool = True,
    until: datetime | None = None,
) -> dict[tuple[int, int], MarkSignals]:
    """Rating, comment, review and note for each wanted (owner, item).

    ``wanted`` maps owner id to item ids after rewrite; content on a rewrite
    source counts for its target. Loaded per owner instead of per mark,
    because Comment and Review have no (owner, item) index. ``item_ids``
    optionally narrows the scan to these raw item ids. Only public content
    counts unless ``public_only`` is False, which is for signals that feed
    nothing but the owner's own list. With ``until`` only content created
    before it counts. Pairs without any signal are absent; use
    ``NO_MARK_SIGNALS``.
    """
    owners = list(wanted)

    def content_rows(model: type[Content], *fields: str) -> Iterator[tuple]:
        qs = model.objects.filter(owner_id__in=owners)
        if public_only:
            qs = qs.filter(visibility=0)
        if item_ids is not None:
            qs = qs.filter(item_id__in=item_ids)
        if until is not None:
            qs = qs.filter(created_time__lt=until)
        return (
            qs.order_by()
            .values_list("owner_id", "item_id", *fields)
            .iterator(chunk_size=20_000)
        )

    grades: dict[tuple[int, int], int] = {}
    for owner_id, item_id, grade in content_rows(Rating, "grade"):
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
        for owner_id, item_id in content_rows(model):
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
_REFILL_INTERVAL = 86400
_STALE_REFRESH_INTERVAL = 600

# Shelves that count as positive interest signal for recommendation training
# and as seeds for personalised recommendations. Wishlist is an explicit
# forward-looking taste signal; dropped is excluded (negative signal).
SHELF_TYPES_AS_SEED = ("wishlist", "progress", "complete")
SHELF_TYPE_NEGATIVE_SEED = "dropped"

# Shelves that mean the user has already engaged with an item, so we should
# never recommend it back to them. Includes "dropped" so we don't re-surface
# things they actively disliked.
SHELF_TYPES_TO_EXCLUDE = ("wishlist", "progress", "complete", "dropped")

# Surfaces that can be shown to anonymous viewers (the rest require a User).
ANON_VISIBLE_KINDS = frozenset({"similar_items"})


def excluded_identity_ids() -> set[int]:
    """Identities whose marks, tags and collections never train recommendations.

    Reuses the existing ``discoverable`` flag on Takahe Identity (also the
    source of truth for ``DiscoverGenerator``). Users uncheck "Include
    profile and posts in discovery" on their account page to opt out of
    being used as a training signal for recommendations. Accounts and
    domains on the site's ``discover_exclude_posts_from`` list are left out
    the same way.
    """
    return set(
        TakaheIdentity.objects.filter(discoverable=False).values_list("pk", flat=True)
    ) | Takahe.get_identity_ids_by_handles(
        SiteConfig.system.discover_exclude_posts_from
    )


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


def viewer_language_codes(viewer) -> list[str]:
    """Language codes the viewer keeps trending items and For you to.

    Empty for no filter: anonymous viewers, members without catalog
    languages, or the site option off.
    """
    if not viewer or not getattr(viewer, "is_authenticated", False):
        return []
    pref = getattr(viewer, "preference", None)
    return pref.catalog_language_codes() if pref else []


_LANGUAGE_BATCH = 1000


def _first_in_languages(ranked: list[int], codes: list[str], n: int) -> list[int]:
    """The first ``n`` ids of ``ranked`` whose items are in ``codes``."""
    wanted = set(codes)
    out: list[int] = []
    for start in range(0, len(ranked), _LANGUAGE_BATCH):
        batch = ranked[start : start + _LANGUAGE_BATCH]
        matched = Item.ids_in_languages(batch, wanted)
        out += [i for i in batch if i in matched]
        if len(out) >= n:
            break
    return out[:n]


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


def _user_shelved_members(
    identity_pk: int, *, until: datetime | None = None
) -> QuerySet[ShelfMember]:
    # _base_manager skips ShelfMemberManager's default annotations (useless
    # here); order_by() keeps subqueries free of any future default ordering.
    qs = ShelfMember._base_manager.filter(
        owner_id=identity_pk,
        parent__shelf_type__in=SHELF_TYPES_TO_EXCLUDE,
    ).order_by()
    if until is not None:
        qs = qs.filter(edited_time__lt=until)
    return qs


def _user_shelved_item_ids(
    identity_pk: int, *, until: datetime | None = None
) -> set[int]:
    return set(
        _user_shelved_members(identity_pk, until=until).values_list(
            "item_id", flat=True
        )
    )


def _user_rewrite_map(
    identity_pk: int, *, until: datetime | None = None
) -> dict[int, int]:
    return _scoped_rewrite_map(
        _user_shelved_members(identity_pk, until=until).values("item_id")
    )


def _user_excluded_item_ids(identity_pk: int) -> set[int]:
    """Shelved items plus what they count toward in training.

    A user who shelved a merged edition or a Production should not be offered
    the surviving edition or the Performance.
    """
    return _with_rewrites(
        _user_shelved_item_ids(identity_pk), _user_rewrite_map(identity_pk)
    )


def _user_dismissed_members(
    user_pk: int, *, until: datetime | None = None
) -> QuerySet[RecommendationDismissal]:
    qs = RecommendationDismissal.objects.filter(user_id=user_pk).order_by()
    if until is not None:
        qs = qs.filter(created_time__lt=until)
    return qs


def _user_dismissed_item_ids(
    user_pk: int, *, until: datetime | None = None
) -> set[int]:
    """Dismissed items plus where their merges have led since."""
    dismissed = _user_dismissed_members(user_pk, until=until)
    ids = set(dismissed.values_list("item_id", flat=True))
    if not ids:
        return ids
    return _with_rewrites(ids, _scoped_rewrite_map(dismissed.values("item_id")))


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


# a Work for an Edition, a show for a TVSeason
GroupKey = tuple[str, int]
_GROUP_CHUNK = 5000


class RecoGroups(NamedTuple):
    keys: dict[int, GroupKey]
    # season number of each TVSeason with a key
    seasons: dict[int, int]


NO_GROUPS = RecoGroups({}, {})


def reco_groups(
    item_ids: Collection[int], cats: Mapping[int, str] | None = None
) -> RecoGroups:
    """Group the editions of one Work and the seasons of one show.

    Members of a group are one thing to recommend, so a list holds one of
    them and their marks count once as seeds. An edition in several Works
    takes the lowest work id. Other items have no key and stand alone.
    ``cats`` saves looking up ids of other categories.
    """

    def of(category: str) -> list[int]:
        if cats is None:
            return list(item_ids)
        return [i for i in item_ids if cats.get(i, category) == category]

    keys: dict[int, GroupKey] = {}
    seasons: dict[int, int] = {}
    through = Work.editions.through
    ids = of(str(Edition.category))
    for start in range(0, len(ids), _GROUP_CHUNK):
        for edition_id, work_id in (
            through.objects.filter(edition_id__in=ids[start : start + _GROUP_CHUNK])
            .order_by("work_id")
            .values_list("edition_id", "work_id")
        ):
            keys.setdefault(edition_id, ("work", work_id))
    ids = of(str(TVSeason.category))
    for start in range(0, len(ids), _GROUP_CHUNK):
        for pk, show_id, number in TVSeason.objects.filter(
            pk__in=ids[start : start + _GROUP_CHUNK], show_id__isnull=False
        ).values_list("pk", "show_id", "season_number"):
            keys[pk] = ("show", show_id)
            if number is not None:
                seasons[pk] = number
    return RecoGroups(keys, seasons)


def _group_of(item_id: int, groups: RecoGroups) -> Hashable:
    return groups.keys.get(item_id, item_id)


def _dedupe_groups(
    ranked: list[tuple[int, float]], groups: RecoGroups
) -> tuple[list[tuple[int, float]], dict[int, int]]:
    """``ranked`` (best first) with one entry per group, at its best score and place.

    A show stands as its lowest numbered season in ``ranked``, so the next
    season of a show in progress or the first of a new one; any other group
    as its best entry. Also returns, for each representative that is not
    the best entry of its group, that best entry.
    """
    if not groups.keys:
        return ranked, {}
    lowest: dict[Hashable, int] = {}
    for pk, _ in ranked:
        key = groups.keys.get(pk)
        number = groups.seasons.get(pk)
        if key is None or number is None:
            continue
        kept = lowest.get(key)
        if kept is None or number < groups.seasons[kept]:
            lowest[key] = pk
    out: list[tuple[int, float]] = []
    stand_in: dict[int, int] = {}
    seen: set[Hashable] = set()
    for pk, score in ranked:
        key = _group_of(pk, groups)
        if key in seen:
            continue
        seen.add(key)
        rep = lowest.get(key, pk)
        if rep != pk:
            stand_in[rep] = pk
        out.append((rep, score))
    return out, stand_in


def dismiss_item(user: User, item: Item) -> None:
    """Stop recommending ``item`` to ``user`` on every surface."""
    RecommendationDismissal.objects.get_or_create(user=user, item=item.final_item)


def forget_for_user(user: User) -> None:
    """Drop the stored personal rows, so the next read computes them again."""
    UserRecommendation.objects.filter(user=user).delete()


def restore_item(user: User, item: Item) -> None:
    """Undo ``dismiss_item`` for every dismissal that resolves to the same item."""
    target = item.final_item.pk
    dismissed = _user_dismissed_members(user.pk)
    rows = list(dismissed.values_list("pk", "item_id"))
    rewrite = _scoped_rewrite_map(dismissed.values("item_id"))
    RecommendationDismissal.objects.filter(
        pk__in=[
            pk
            for pk, item_id in rows
            if item_id == item.pk or rewrite.get(item_id, item_id) == target
        ]
    ).delete()


def similar_items(item: Item, viewer=None, limit: int = 10) -> list[Item]:
    """Return up to ``limit`` items similar to ``item``.

    Excludes items the viewer has already shelved (any state) or dismissed.
    Drops deleted and merged items, and with ``reco_group_items`` the other
    editions or seasons of ``item`` and all but one of any other group. No
    author/owner visibility filter needed: ItemSimilarity is built from
    public marks only. The viewer's catalog languages do not apply: a
    similar item is wanted in any language.
    """
    rows = list(
        ItemSimilarity.objects.filter(source=item, method=ItemSimilarity.METHOD_BLENDED)
        .order_by("-score")
        .values_list("target_id", "score")[: limit * 4]
    )
    if not rows:
        return []
    exclude: set[int] = set()
    if viewer and viewer.is_authenticated and getattr(viewer, "identity", None):
        exclude = _user_excluded_item_ids(
            viewer.identity.pk
        ) | _user_dismissed_item_ids(viewer.pk)
    qs = _live_items(Item.objects.filter(pk__in=[iid for iid, _ in rows]))
    by_id = {i.pk: i for i in qs}
    ranked = [(iid, sc) for iid, sc in rows if iid in by_id and iid not in exclude]
    if SiteConfig.system.reco_group_items:
        groups = reco_groups([item.pk, *(iid for iid, _ in ranked)])
        own = groups.keys.get(item.pk)
        if own is not None:
            ranked = [e for e in ranked if groups.keys.get(e[0]) != own]
        ranked, _ = _dedupe_groups(ranked, groups)
    return [by_id[iid] for iid, _ in ranked[:limit]]


def _categories_of(item_ids: Collection[int]) -> dict[int, str]:
    """Category value per item id, read off the concrete class of each row."""
    by_ctype = {
        ct: str(cls.category)
        for cls, ct in item_content_types().items()
        if getattr(cls, "category", None) is not None
    }
    ids = list(item_ids)
    out: dict[int, str] = {}
    for start in range(0, len(ids), 5000):
        rows = Item.objects.filter(pk__in=ids[start : start + 5000]).values_list(
            "pk", "polymorphic_ctype_id"
        )
        out.update((pk, by_ctype[ct]) for pk, ct in rows if ct in by_ctype)
    return out


# (source, target, score) of blended neighbours for the given sources
SimilarityLookup = Callable[[Collection[int]], Iterable[tuple[int, int, float]]]


# picks ``quota`` of a category's entries (best first), each with the score
# to store, in the order picked
Diversify = Callable[[list[tuple[int, float]], int], list[tuple[int, float]]]


def _balanced_top(
    ranked: list[tuple[int, float]],
    cats: dict[int, str],
    weights: dict[str, float],
    n: int,
    diversify: Diversify | None = None,
) -> list[tuple[int, float]]:
    """Top ``n`` of ``ranked`` (best first) with category slots in proportion to ``weights``.

    Slots go by largest remainder, only to categories that have candidates.
    A category short of its slots hands the rest to the best remaining items
    overall, and a category without weight only gets such leftovers.
    ``diversify`` fills each category's slots instead of its best entries,
    and a leftover then scores no higher than that category's picks.
    """
    if len(ranked) <= n and diversify is None:
        return list(ranked)
    by_cat: dict[str, list[tuple[int, float]]] = {}
    for entry in ranked:
        cat = cats.get(entry[0])
        if cat:
            by_cat.setdefault(cat, []).append(entry)
    w = {c: weights[c] for c in by_cat if weights.get(c, 0) > 0}
    total = sum(w.values())
    chosen: set[int] = set()
    rescored: dict[int, float] = {}
    if total:
        exact = {c: n * v / total for c, v in w.items()}
        quota = {c: int(x) for c, x in exact.items()}
        spare = n - sum(quota.values())
        for c in sorted(exact, key=lambda c: exact[c] - quota[c], reverse=True)[:spare]:
            quota[c] += 1
        for c, q in quota.items():
            if diversify is None:
                chosen.update(pk for pk, _ in by_cat[c][:q])
            else:
                rescored.update(diversify(by_cat[c], q))
    chosen.update(rescored)
    for pk, _ in ranked:
        if len(chosen) >= n:
            break
        if pk in cats:
            chosen.add(pk)
    if not rescored:
        return [e for e in ranked if e[0] in chosen]
    floor: dict[str, float] = {}
    for pk, sc in rescored.items():
        floor[cats[pk]] = min(sc, floor.get(cats[pk], sc))
    return [
        (pk, rescored[pk] if pk in rescored else min(sc, floor.get(cats[pk], sc)))
        for pk, sc in ranked
        if pk in chosen
    ]


def pair_similarity(
    neighbours: SimilarityLookup, item_ids: Collection[int]
) -> dict[tuple[int, int], float]:
    """Blended similarity of each pair within ``item_ids``, the higher direction.

    Keyed by (lower id, higher id); a pair neither item lists is absent.
    """
    wanted = set(item_ids)
    out: dict[tuple[int, int], float] = {}
    for src, tgt, score in neighbours(list(wanted)):
        if tgt in wanted:
            key = (src, tgt) if src < tgt else (tgt, src)
            if score > out.get(key, 0.0):
                out[key] = score
    return out


# a category's slots are filled from this many times as many of its best
MMR_POOL_FACTOR = 4


def _mmr(
    entries: list[tuple[int, float]],
    quota: int,
    lam: float,
    neighbours: SimilarityLookup,
    overflow: Collection[int] = (),
) -> list[tuple[int, float]]:
    """Maximal marginal relevance: ``quota`` of ``entries`` (best first), varied.

    Each pick maximises its score less ``lam`` times the best score times
    its highest similarity to an earlier pick, and stores that value, which
    never rises from one pick to the next. Entries past their seed's slots
    (``overflow``) are picked only once the others run out.
    """
    pool = entries[: quota * MMR_POOL_FACTOR]
    if not pool:
        return []
    top = max(sc for _, sc in pool)
    sims = pair_similarity(neighbours, [pk for pk, _ in pool])
    closest = {pk: 0.0 for pk, _ in pool}
    picked: list[tuple[int, float]] = []
    stored = top
    for tier in (
        [e for e in pool if e[0] not in overflow],
        [e for e in pool if e[0] in overflow],
    ):
        while tier and len(picked) < quota:
            values = [sc - lam * top * closest[pk] for pk, sc in tier]
            i = max(range(len(tier)), key=values.__getitem__)
            pk, _ = tier.pop(i)
            # the second tier can start above the end of the first
            stored = min(stored, values[i])
            picked.append((pk, stored))
            for other in closest:
                key = (pk, other) if pk < other else (other, pk)
                closest[other] = max(closest[other], sims.get(key, 0.0))
    return picked


# a seed never fades below this share of its weight
MIN_SEED_RECENCY = 0.25


def aggregate_contributions(contributions: Iterable[float], decay: float) -> float:
    """Sum of the contributions, strongest first, the i-th (from 0) times ``decay**i``.

    ``decay`` 1 is the plain sum; lower values let a cluster of related
    seeds add less over one strong link.
    """
    total = 0.0
    factor = 1.0
    for c in sorted(contributions, reverse=True):
        total += c * factor
        factor *= decay
    return total


def seed_recency_factor(age_days: float, half_life_days: int) -> float:
    """Share of its weight a seed keeps at ``age_days`` old; 1 when decay is off."""
    if half_life_days <= 0:
        return 1.0
    return max(MIN_SEED_RECENCY, 0.5 ** (max(age_days, 0.0) / half_life_days))


def _split_per_seed(
    ranked: list[tuple[int, float]],
    seeds_by_target: Mapping[int, list[int]],
    slots: int,
    seed_groups: RecoGroups = NO_GROUPS,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """(admitted, overflow): ``ranked`` split at ``slots`` targets per strongest seed.

    Seeds of one group share their slots. Both parts keep the order of
    ``ranked``; ``slots`` 0 admits everything.
    """
    if slots <= 0:
        return ranked, []
    used: dict[Hashable, int] = {}
    admitted: list[tuple[int, float]] = []
    overflow: list[tuple[int, float]] = []
    for entry in ranked:
        seeds = seeds_by_target.get(entry[0])
        if not seeds:
            admitted.append(entry)
            continue
        lead = _group_of(seeds[0], seed_groups)
        n = used.get(lead, 0)
        if n < slots:
            used[lead] = n + 1
            admitted.append(entry)
        else:
            overflow.append(entry)
    return admitted, overflow


def _cap_per_seed(
    ranked: list[tuple[int, float]],
    seeds_by_target: Mapping[int, list[int]],
    slots: int,
    seed_groups: RecoGroups = NO_GROUPS,
) -> list[tuple[int, float]]:
    """``ranked`` with at most ``slots`` targets per strongest seed up front.

    The targets past a seed's slots follow all the others in their own
    order, so the list is reordered, never shortened. One prolific seed
    then cannot fill the whole list. ``slots`` 0 keeps the order.
    """
    admitted, overflow = _split_per_seed(ranked, seeds_by_target, slots, seed_groups)
    return admitted + overflow


def recommendable_for_user(
    user_pk: int, item_ids: Sequence[int], cats: Mapping[int, str] | None = None
) -> set[int]:
    """The ids of ``item_ids`` the member's category and language settings allow.

    Categories the site hides or the member does not search stay out, as do
    items without a category, and so do items outside the member's catalog
    languages when the site applies them. ``cats`` saves the category
    lookup when the caller already has it.
    """
    sys = SiteConfig.system
    pref = Preference.objects.filter(user_id=user_pk).first()
    hidden = set(sys.hidden_categories) | set(pref.hidden_categories if pref else [])
    if cats is None:
        cats = _categories_of(item_ids)
    allowed = [i for i in item_ids if cats.get(i) not in (None, *hidden)]
    if sys.discover_user_languages and pref:
        codes = pref.catalog_language_codes()
        if codes:
            return set(_first_in_languages(allowed, codes, len(allowed)))
    return set(allowed)


def stored_similarity(source_ids: Collection[int]) -> Iterable[tuple[int, int, float]]:
    """Blended neighbours of ``source_ids`` as stored by the weekly build."""
    return ItemSimilarity.objects.filter(
        source_id__in=source_ids, method=ItemSimilarity.METHOD_BLENDED
    ).values_list("source_id", "target_id", "score")


def user_reco_exclusions(
    user_pk: int,
    identity_pk: int,
    rewrite: dict[int, int] | None = None,
    *,
    until: datetime | None = None,
) -> tuple[set[int], set[int]]:
    """(dismissed, excluded): items ``compute_for_user`` never returns.

    Excluded are shelved and dismissed items, what they count toward in
    training and their sibling editions (same Work). ``rewrite`` defaults
    to the member's scoped rewrite map.
    """
    if rewrite is None:
        rewrite = _user_rewrite_map(identity_pk, until=until)
    dismissed = _user_dismissed_item_ids(user_pk, until=until)
    hidden = (
        _with_rewrites(_user_shelved_item_ids(identity_pk, until=until), rewrite)
        | dismissed
    )
    return dismissed, hidden | _sibling_edition_ids(hidden)


# window of public marks a popular item is counted over
POPULARITY_WINDOW_DAYS = 90
# popular items kept beyond any list length, so that a member's own items
# can be skipped and the list still fill up
POPULARITY_SPARE = 1000
_POPULAR_KEY = "reco:popular"
_POPULAR_TTL = 86400


def popular_items(
    cutoff: datetime, limit: int, excluded_owners: Collection[int] = ()
) -> list[int]:
    """Items by distinct owners of public seed-shelf marks in the window before ``cutoff``."""
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
    if excluded_owners:
        qs = qs.exclude(owner_id__in=excluded_owners)
    return list(
        qs.order_by()
        .values("item_id")
        .annotate(n=Count("owner_id", distinct=True))
        .order_by("-n", "item_id")
        .values_list("item_id", flat=True)[:limit]
    )


class PopularItems(NamedTuple):
    ids: list[int]
    cats: dict[int, str]
    groups: RecoGroups


def popular_fill(cutoff: datetime | None = None) -> PopularItems:
    """Popular items that fill a cold-start list, most popular first.

    Marks of owners left out of training do not count.
    """
    ids = popular_items(
        cutoff or timezone.now(), POPULARITY_SPARE, excluded_identity_ids()
    )
    cats = _categories_of(ids)
    return PopularItems(ids, cats, reco_groups(ids, cats))


def refresh_popular_fill() -> PopularItems:
    fill = popular_fill()
    cache.set(_POPULAR_KEY, fill, timeout=_POPULAR_TTL)
    return fill


def cached_popular_fill() -> PopularItems:
    """``popular_fill`` as of now, computed at most once per ``_POPULAR_TTL``."""
    fill = cache.get(_POPULAR_KEY)
    return fill if fill is not None else refresh_popular_fill()


def _cold_start_fill(
    user_pk: int,
    popular: PopularItems,
    skip: set[int],
    taken: set[Hashable],
    grouped: bool,
    below: float,
) -> list[tuple[int, float]]:
    """Popular items for the member, one per group not ``taken``, scored under ``below``."""
    candidates = [i for i in popular.ids if i not in skip]
    allowed = recommendable_for_user(user_pk, candidates, popular.cats)
    ranked = [(i, 0.0) for i in candidates if i in allowed]
    if grouped:
        ranked, _ = _dedupe_groups(ranked, popular.groups)
    picked: list[int] = []
    for i, _ in ranked:
        key = _group_of(i, popular.groups) if grouped else i
        if key not in taken:
            taken.add(key)
            picked.append(i)
    n = len(picked)
    return [(i, below * (n - k) / (n + 1)) for k, i in enumerate(picked)]


def compute_for_user(
    user_pk: int,
    identity_pk: int,
    *,
    until: datetime | None = None,
    similarity: SimilarityLookup | None = None,
    popular: PopularItems | None = None,
) -> list[UserRecommendation]:
    """Score candidate items for one user, returning unsaved UserRecommendation rows.

    Seeds are the user's recent marks of any visibility: the rows are shown
    to nobody else. A seed rated well below the user's own average, a
    dropped item and a dismissed item lower the score of their neighbours.
    A member with fewer seed groups than ``reco_cold_start_seeds`` gets
    popular items after the rows within their seed's slots and before the
    rest.

    ``until`` replays the member as of that time for offline evaluation:
    only marks, content and dismissals before it count, and it stands in
    for now in the seed decay. ``similarity`` replaces the stored
    neighbours, e.g. with a build bounded by the same time. ``popular``
    replaces the cached popular items, e.g. counted before the same time.
    """
    sys = SiteConfig.system
    seed_cap = sys.reco_per_user_seed_cap
    top_n = sys.reco_user_top_n
    neighbours = similarity or stored_similarity

    seed_qs = ShelfMember._base_manager.filter(
        owner_id=identity_pk,
        parent__shelf_type__in=SHELF_TYPES_AS_SEED,
    )
    if until is not None:
        seed_qs = seed_qs.filter(edited_time__lt=until)
    raw_seeds = list(
        seed_qs.order_by("-edited_time").values_list(
            "item_id", "edited_time", "parent__shelf_type"
        )[: seed_cap * 2]
    )
    if not raw_seeds:
        return []
    # Rewrite seeds the same way the similarity matrix aggregates marks.
    # Seeds are a subset of the shelved items the scoped map starts from.
    rewrite = _user_rewrite_map(identity_pk, until=until)
    # the rest of the seed slots go to the best rated marks before them
    top_rated = min(max(sys.reco_seed_top_rated, 0), seed_cap)
    seeds: list[int] = []
    seed_time: dict[int, datetime] = {}
    seed_shelf: dict[int, str] = {}
    for sid, edited, shelf in raw_seeds:
        if len(seeds) >= seed_cap - top_rated:
            break
        mapped = rewrite.get(sid, sid)
        if mapped in seed_time:
            continue
        # newest first, so this is the latest mark counting toward the seed
        seed_time[mapped] = edited
        seed_shelf[mapped] = shelf
        seeds.append(mapped)
    recent = len(seeds)
    raw_ids = {sid for sid, _, _ in raw_seeds}
    if top_rated:
        rated = Rating.objects.filter(
            owner_id=identity_pk, grade__gt=0, item_id__in=seed_qs.values("item_id")
        )
        if until is not None:
            rated = rated.filter(created_time__lt=until)
        best = list(
            rated.order_by("-grade", "-edited_time").values_list("item_id", flat=True)[
                : top_rated * 2 + recent
            ]
        )
        marks = {
            sid: (edited, shelf)
            for sid, edited, shelf in seed_qs.filter(item_id__in=best).values_list(
                "item_id", "edited_time", "parent__shelf_type"
            )
        }
        for sid in best:
            if len(seeds) >= seed_cap:
                break
            mapped = rewrite.get(sid, sid)
            if mapped in seed_time or sid not in marks:
                continue
            seed_time[mapped], seed_shelf[mapped] = marks[sid]
            seeds.append(mapped)
            raw_ids.add(sid)
    seed_set = set(seeds)
    # content written on a rewrite target counts too, as it does in training
    signals = load_mark_signals(
        {identity_pk: seed_set},
        rewrite,
        item_ids=raw_ids | {rewrite[s] for s in raw_ids if s in rewrite},
        public_only=False,
        until=until,
    )
    seed_signals = [signals.get((identity_pk, s), NO_MARK_SIGNALS) for s in seeds]
    # the best rated seeds would lift the mean the others are measured by
    user_mean = user_mean_grade(grade for grade, _, _, _ in seed_signals[:recent])
    now = until or timezone.now()
    positive: list[int] = []
    low_rated: list[int] = []
    seed_weight: dict[int, float] = {}
    for sid, (grade, has_comment, has_review, has_note) in zip(seeds, seed_signals):
        if (
            grade
            and user_mean is not None
            and grade <= user_mean - sys.reco_negative_rating_gap
        ):
            low_rated.append(sid)
            continue
        positive.append(sid)
        age_days = (now - seed_time[sid]).total_seconds() / 86400
        seed_weight[sid] = reco_mark_weight(
            sys,
            grade,
            user_mean,
            has_comment=has_comment,
            has_review=has_review,
            has_note=has_note,
        ) * seed_recency_factor(age_days, sys.reco_seed_half_life_days)
        if seed_shelf[sid] == "wishlist":
            seed_weight[sid] *= sys.reco_wishlist_seed_weight
    # a seed weighed to nothing neither leads, fills quotas nor warms a start
    positive = [sid for sid in positive if seed_weight[sid] > 0]
    if not positive:
        return []
    # Precompute only; a sibling marked later may dupe until the next refresh.
    dismissed, excluded = user_reco_exclusions(
        user_pk, identity_pk, rewrite, until=until
    )
    neg_weight = sys.reco_negative_weight
    # a dismissal may only mean the member has it elsewhere
    dismissal_weight = (
        sys.reco_dismissal_weight if sys.reco_dismissal_weight >= 0 else neg_weight
    )
    negative_weight: dict[int, float] = {}
    if neg_weight > 0:
        dropped_qs = ShelfMember._base_manager.filter(
            owner_id=identity_pk, parent__shelf_type=SHELF_TYPE_NEGATIVE_SEED
        )
        if until is not None:
            dropped_qs = dropped_qs.filter(edited_time__lt=until)
        dropped = dropped_qs.order_by("-edited_time").values_list("item_id", flat=True)[
            :seed_cap
        ]
        for i in [*low_rated, *(rewrite.get(i, i) for i in dropped)]:
            negative_weight.setdefault(i, neg_weight)
    if dismissal_weight > 0:
        # dismissals have no order here, so they come last under the cap
        for i in sorted(dismissed):
            negative_weight.setdefault(i, dismissal_weight)
    negatives = [i for i in negative_weight if i not in seed_weight][:seed_cap]
    group_items = sys.reco_group_items
    # seeds of one group (editions of a Work, seasons of a show) count once
    seed_groups = reco_groups(seed_set | set(negatives)) if group_items else NO_GROUPS
    cold = (
        len({_group_of(s, seed_groups) for s in positive}) < sys.reco_cold_start_seeds
    )

    # the strongest (contribution, seed) of each seed group, per target
    contribs: dict[int, dict[Hashable, tuple[float, int]]] = {}
    for src, tgt, score in neighbours(positive):
        if tgt in excluded or tgt in seed_set:
            continue
        entry = (seed_weight[src] * score, src)
        by_group = contribs.setdefault(tgt, {})
        key = _group_of(src, seed_groups)
        kept = by_group.get(key)
        if kept is None or entry > kept:
            by_group[key] = entry
    if not contribs and not cold:
        return []
    strongest = {
        tgt: nlargest(SEED_CONTRIB_CAP, by_group.values())
        for tgt, by_group in contribs.items()
    }
    del contribs
    decay = sys.reco_seed_agg_decay
    scores = {
        tgt: aggregate_contributions((c for c, _ in top), decay)
        for tgt, top in strongest.items()
    }
    if negatives:
        penalties: dict[int, dict[Hashable, float]] = {}
        for src, tgt, score in neighbours(negatives):
            if tgt not in scores:
                continue
            by_group = penalties.setdefault(tgt, {})
            key = _group_of(src, seed_groups)
            by_group[key] = max(by_group.get(key, 0.0), negative_weight[src] * score)
        for tgt, by_group in penalties.items():
            if sys.reco_cap_negatives:
                scores[tgt] -= sum(nlargest(SEED_CONTRIB_CAP, by_group.values()))
            else:
                scores[tgt] -= sum(by_group.values())
        scores = {tgt: sc for tgt, sc in scores.items() if sc > 0}
        if not scores and not cold:
            return []
    seeds_by_target = {tgt: [src for _, src in strongest[tgt][:3]] for tgt in scores}

    ranked = sorted(scores.items(), key=lambda t: t[1], reverse=True)
    cats = _categories_of([t for t, _ in ranked] + positive)
    allowed = recommendable_for_user(user_pk, [t for t, _ in ranked], cats)
    ranked = [(t, sc) for t, sc in ranked if t in allowed]
    if not ranked and not cold:
        return []
    target_groups = NO_GROUPS
    if group_items:
        target_groups = reco_groups([t for t, _ in ranked], cats)
        ranked, stand_in = _dedupe_groups(ranked, target_groups)
        for rep, best in stand_in.items():
            seeds_by_target[rep] = seeds_by_target[best]
    admitted, overflow = _split_per_seed(
        ranked, seeds_by_target, sys.reco_per_seed_slots, seed_groups
    )
    if cold:
        popular = popular or cached_popular_fill()
        fill = _cold_start_fill(
            user_pk,
            popular,
            excluded | seed_set | scores.keys(),
            {_group_of(t, target_groups) for t, _ in ranked},
            group_items,
            min((sc for _, sc in admitted), default=1.0),
        )
        cats.update((i, popular.cats[i]) for i, _ in fill)
        admitted += fill
        if not admitted:
            return []
    ranked = admitted + overflow
    # slots follow the mix of the member's own marks, a seed group counting once
    weights: dict[str, float] = {}
    counted: set[Hashable] = set()
    for sid in positive:
        cat = cats.get(sid)
        key = _group_of(sid, seed_groups)
        if cat and key not in counted:
            counted.add(key)
            weights[cat] = weights.get(cat, 0) + 1
    diversify: Diversify | None = None
    if sys.reco_diversity_lambda > 0:
        diversify = partial(
            _mmr,
            lam=sys.reco_diversity_lambda,
            neighbours=neighbours,
            overflow={pk for pk, _ in overflow},
        )
    top = _balanced_top(ranked, cats, weights, top_n, diversify)
    if diversify is not None:
        # in the order serving ranks them: by the lowered score, capped per seed
        top = _cap_per_seed(
            sorted(top, key=lambda e: e[1], reverse=True),
            seeds_by_target,
            sys.reco_per_seed_slots,
            seed_groups,
        )
    rows = [
        UserRecommendation(
            user_id=user_pk,
            item_id=tgt,
            score=score,
            seed_item_ids=seeds_by_target.get(tgt, []),
            category=cats[tgt],
        )
        for tgt, score in top
    ]
    return rows


def _refresh_lazy(
    user_pk: int, identity_pk: int, keep_when_empty: bool = False
) -> bool:
    """Lazy on-demand recompute for one user.

    Guarded by a cache-based lock so concurrent requests for the same user
    don't dogpile compute + write. Returns True if this caller replaced the
    rows, False if another request already holds the lock (skip-and-serve-
    stale) or, with ``keep_when_empty``, the compute found nothing.
    """
    lock_key = f"reco:lazy_refresh:{user_pk}"
    if not cache.add(lock_key, "1", timeout=_LAZY_LOCK_TTL):
        return False
    try:
        rows = compute_for_user(user_pk, identity_pk)
        if keep_when_empty and not rows:
            return False
        with transaction.atomic():
            UserRecommendation.objects.filter(user_id=user_pk).delete()
            if rows:
                UserRecommendation.objects.bulk_create(rows, ignore_conflicts=True)
        return True
    finally:
        cache.delete(lock_key)


def _refill(user_pk: int, identity_pk: int) -> bool:
    """Recompute for a member who shelved or dismissed most of the stored rows.

    At most once a day per member. When nothing new turns up the old rows are
    kept, so an exhausted member does not recompute on every request. A failed
    attempt is retried after ``_LAZY_LOCK_TTL`` rather than a day later.
    """
    key = f"reco:refill:{user_pk}"
    if not cache.add(key, "1", timeout=_REFILL_INTERVAL):
        return False
    try:
        rows = compute_for_user(user_pk, identity_pk)
        if not rows:
            return False
        with transaction.atomic():
            UserRecommendation.objects.filter(user_id=user_pk).delete()
            UserRecommendation.objects.bulk_create(rows, ignore_conflicts=True)
    except Exception:
        cache.set(key, "1", timeout=_LAZY_LOCK_TTL)
        raise
    return True


def _shelf_changed_since(identity_pk: int, when: datetime) -> bool:
    """Whether a mark or a rating of the member changed after ``when``.

    A rating-only edit touches the Rating row but not the ShelfMember, and
    it can turn a seed negative, so both are checked.
    """
    for model in (ShelfMember, Rating):
        newest = (
            model._base_manager.filter(owner_id=identity_pk)
            .order_by("-edited_time")
            .values_list("edited_time", flat=True)
            .first()
        )
        if newest is not None and newest > when:
            return True
    return False


def _refresh_stale(user_pk: int, identity_pk: int) -> bool:
    """Recompute rows older than the member's last shelf change.

    At most once per ``_STALE_REFRESH_INTERVAL`` per member, so marking a
    run of items does not recompute on every request. The old rows stay
    when the compute finds nothing.
    """
    key = f"reco:stale_refresh:{user_pk}"
    if not cache.add(key, "1", timeout=_STALE_REFRESH_INTERVAL):
        return False
    return _refresh_lazy(user_pk, identity_pk, keep_when_empty=True)


def _cached_user_rows(user_pk: int, ttl_days: int) -> list[UserRecommendation]:
    qs = UserRecommendation.objects.filter(user_id=user_pk).order_by("-score")
    rows = list(qs)
    if not rows:
        return []
    horizon = timezone.now() - timedelta(days=ttl_days)
    if rows[0].computed_at < horizon:
        return []
    return rows


def _attach_seed_items(items: list[Item], seed_ids: dict[int, list[int]]) -> None:
    """Set ``reco_seed_items`` on each item from one query; deleted seeds drop out."""
    wanted = {sid for item in items for sid in seed_ids.get(item.pk, [])}
    seeds = (
        {s.pk: s for s in Item.objects.filter(pk__in=wanted, is_deleted=False)}
        if wanted
        else {}
    )
    for item in items:
        item.reco_seed_items = [
            seeds[sid] for sid in seed_ids.get(item.pk, []) if sid in seeds
        ]


def for_you(
    viewer, category: str | None = None, limit: int = 30, seeds: bool = True
) -> list[Item]:
    """Return personalised recommendations for the viewer.

    The best scored rows are picked and returned newest in the catalog
    first (by id), not in score order. With ``seeds``, each item carries
    ``reco_seed_items``, the marked items it was found through, strongest
    first. Stored rows are computed again when missing,
    older than ``reco_lazy_ttl_days``, or older than the viewer's newest
    shelf change. The last is at most once per ``_STALE_REFRESH_INTERVAL``
    per viewer; until then, or when that compute fails or finds nothing,
    the old rows are served.
    """
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
    elif _shelf_changed_since(identity.pk, rows[0].computed_at):
        try:
            if _refresh_stale(viewer.pk, identity.pk):
                rows = _cached_user_rows(viewer.pk, sys.reco_lazy_ttl_days)
        except Exception as e:
            logger.exception(f"Stale reco refresh failed for user {viewer.pk}: {e}")
    # filter before cutting to ``limit``, so the rows stored beyond it
    # (reco_user_top_n) fill the places of shelved, dismissed or dead items
    skip = _user_excluded_item_ids(identity.pk) | _user_dismissed_item_ids(viewer.pk)
    codes = viewer_language_codes(viewer)
    if codes:
        # rows stored before the site turned the language filter on; out of
        # language they count as used up, so a refill replaces them
        stored = [r.item_id for r in rows if r.item_id not in skip]
        skip |= set(stored) - Item.ids_in_languages(stored, set(codes))
    usable = sum(1 for r in rows if r.item_id not in skip)
    # the stored rows ran out because of shelving or dismissing, not because
    # the list is short; a fresh compute skips those and reaches further
    if usable < limit and usable < len(rows):
        try:
            if _refill(viewer.pk, identity.pk):
                rows = _cached_user_rows(viewer.pk, sys.reco_lazy_ttl_days)
        except Exception as e:
            logger.exception(f"Reco refill failed for user {viewer.pk}: {e}")
    if category:
        rows = [r for r in rows if r.category == category]
    if not rows:
        return []
    candidates = [r.item_id for r in rows if r.item_id not in skip]
    if not candidates:
        return []
    live = set(
        _live_items(Item.objects.filter(pk__in=candidates))
        .order_by()
        .values_list("pk", flat=True)
    )
    live_rows = [r for r in rows if r.item_id in live and r.item_id not in skip]
    seed_ids = {r.item_id: r.seed_item_ids for r in live_rows}
    row_cats = {r.item_id: r.category for r in live_rows}
    ranked = [(r.item_id, r.score) for r in live_rows]
    groups = NO_GROUPS
    if sys.reco_group_items:
        # rows stored before grouping, or a sibling shelved since
        groups = reco_groups(
            {pk for pk, _ in ranked} | {ids[0] for ids in seed_ids.values() if ids},
            row_cats,
        )
        ranked, stand_in = _dedupe_groups(ranked, groups)
        for rep, best in stand_in.items():
            seed_ids[rep] = seed_ids[best]
    # stored rows come back by score, so the per-seed cap applies again
    ranked = _cap_per_seed(ranked, seed_ids, sys.reco_per_seed_slots, groups)
    if category:
        target_ids = [pk for pk, _ in ranked][:limit]
    else:
        # the stored mix already follows the member's marks
        mix: dict[str, float] = {}
        for pk, _ in ranked:
            mix[row_cats[pk]] = mix.get(row_cats[pk], 0) + 1
        picked = _balanced_top(ranked, row_cats, mix, limit)
        target_ids = [pk for pk, _ in picked]
    # score picks the items; newest in the catalog are shown first
    target_ids.sort(reverse=True)
    by_id = {i.pk: i for i in Item.objects.filter(pk__in=target_ids)}
    items = [by_id[tid] for tid in target_ids if tid in by_id]
    if seeds:
        _attach_seed_items(items, seed_ids)
    return items


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
        .exclude(item_id__in=_user_dismissed_members(viewer.pk).values("item_id"))
    )
    if excluded_ctypes:
        qs = qs.exclude(item__polymorphic_ctype_id__in=excluded_ctypes)
    return list(
        qs.values("item_id")
        .annotate(c=Count("owner_id", distinct=True), latest=Max("edited_time"))
        .order_by("-c", "-latest")
        .values_list("item_id", flat=True)[: limit * 2]
    )


def from_your_circles(
    viewer, category: str | None = None, limit: int = 30
) -> list[Item]:
    """Items recently marked by people the viewer follows, ranked by distinct shelvers.

    The ranked ids are cached per viewer for ``_CIRCLES_TTL``, so follow
    changes and new marks by followees show up late; items the viewer shelved
    or dismissed since then are dropped on every read. Respects visibility via
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
    skip = set(
        _user_shelved_members(identity.pk)
        .filter(item_id__in=target_ids)
        .values_list("item_id", flat=True)
    ) | _user_dismissed_item_ids(viewer.pk)
    items_qs = _live_items(
        Item.objects.filter(pk__in=[i for i in target_ids if i not in skip])
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
    seen: dict[int, Item] = {}
    out: list[Item] = []
    a_iter = iter(a)
    b_iter = iter(b)
    while len(out) < limit:
        progressed = False
        for it in (next(a_iter, None), next(b_iter, None)):
            if it is None:
                continue
            progressed = True
            kept = seen.get(it.pk)
            if kept is not None:
                # the circles copy may have come first; keep the seeds
                if it.reco_seed_items and not kept.reco_seed_items:
                    kept.reco_seed_items = it.reco_seed_items
                continue
            seen[it.pk] = it
            out.append(it)
            if len(out) >= limit:
                break
        if not progressed:
            break
    return out
