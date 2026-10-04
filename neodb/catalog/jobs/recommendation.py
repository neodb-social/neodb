import logging
import math
from array import array
from collections import defaultdict
from collections.abc import Collection, Hashable, Iterable, Iterator
from datetime import datetime, timedelta
from heapq import merge, nlargest
from itertools import groupby
from operator import itemgetter
from typing import NamedTuple, TypeVar

import numpy as np
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.utils import timezone
from scipy.sparse import csc_matrix, csr_matrix

from catalog.models import (
    CreditRole,
    Item,
    ItemCredit,
    ItemSimilarity,
    UserRecommendation,
    item_categories,
    item_content_types,
)
from catalog.recommendation import (
    NO_MARK_SIGNALS,
    SHELF_TYPES_AS_SEED,
    compute_for_user,
    excluded_target_ctype_ids,
    load_mark_signals,
    production_to_performance_map,
    reco_mark_weight,
    training_rewrite_map,
    user_mean_grade,
)
from common.models import BaseJob, JobManager, SiteConfig
from journal.models import CollectionMember, ShelfMember, Tag, TagMember
from takahe.models import Identity as TakaheIdentity
from users.models import APIdentity

logger = logging.getLogger(__name__)

# owners whose kept marks are weighed together; bounds the signal lookup
_WEIGH_OWNER_BATCH = 200
# source columns per item-item product block, bounds peak memory
_SOURCE_BLOCK = 256
# sources replaced per transaction
_WRITE_SOURCE_BATCH = 200
# ids per IN list
_ID_CHUNK = 10_000


class UserMarks(NamedTuple):
    """One user's kept marks, item ids and weights in parallel compact arrays."""

    items: "array[int]"
    weights: "array[float]"


def _non_discoverable_identity_ids() -> set[int]:
    """Identities that have opted out of discovery features.

    Reuses the existing ``discoverable`` flag on Takahe Identity (also the
    source of truth for ``DiscoverGenerator``). Users uncheck "Include
    profile and posts in discovery" on their account page to opt out of
    being used as a training signal for recommendations.
    """
    return set(
        TakaheIdentity.objects.filter(discoverable=False).values_list("pk", flat=True)
    )


def _coo_to_csc(
    rows: "array[int]", cols: "array[int]", data: "array[float]", shape: tuple
) -> csc_matrix:
    return csc_matrix(
        (
            np.frombuffer(data, dtype=np.float32),
            (np.frombuffer(rows, dtype=np.int32), np.frombuffer(cols, dtype=np.int32)),
        ),
        shape=shape,
    )


def _cosine_topk(
    mc: csc_matrix,
    sources: np.ndarray,
    target_mask: np.ndarray,
    top_k: int,
    shrinkage: float,
) -> Iterator[tuple[int, np.ndarray, np.ndarray]]:
    """Top-K cosine neighbours of each source column of ``mc`` (rows x items).

    Yields ``(source_col, target_cols, scores)`` with scores descending. With
    ``shrinkage`` > 0 a score is multiplied by ``n / (n + shrinkage)``, where
    ``n`` counts the rows (users) holding both items. The item-item product
    is computed for ``_SOURCE_BLOCK`` source columns at a time, so peak
    memory is one block, not the whole item-item matrix.
    """
    norms = np.sqrt(np.asarray(mc.power(2).sum(axis=0)).ravel()).astype(np.float32)
    # transposes of CSC matrices are CSR views, no copy
    mt = mc.T
    bc: csc_matrix | None = None
    bt: csr_matrix | None = None
    if shrinkage > 0:
        # shares mc's index arrays on purpose; neither matrix is modified
        bc = csc_matrix((np.ones_like(mc.data), mc.indices, mc.indptr), shape=mc.shape)
        bt = bc.T
    for start in range(0, len(sources), _SOURCE_BLOCK):
        block = sources[start : start + _SOURCE_BLOCK]
        r = csc_matrix(mt @ mc[:, block])
        r.sort_indices()
        block_col = np.repeat(np.arange(len(block)), np.diff(r.indptr))
        r.data /= norms[r.indices] * norms[block][block_col]
        if bt is not None and bc is not None:
            n = csc_matrix(bt @ bc[:, block])
            n.sort_indices()
            factor = n.data / (n.data + np.float32(shrinkage))
            # nonzero weights give B the same sparsity pattern as M
            if np.array_equal(n.indptr, r.indptr) and np.array_equal(
                n.indices, r.indices
            ):
                r.data *= factor
            else:
                logger.warning(
                    f"similarity count pattern differs: {r.nnz} vs {n.nnz} entries"
                )
                n.data = factor
                r = csc_matrix(r.multiply(n))
                r.sort_indices()
        for j, src in enumerate(block):
            lo, hi = r.indptr[j], r.indptr[j + 1]
            idx = r.indices[lo:hi]
            val = r.data[lo:hi]
            keep = target_mask[idx] & (idx != src)
            if not keep.any():
                continue
            idx = idx[keep]
            val = val[keep]
            if val.size > top_k:
                cut = np.argpartition(-val, top_k)[:top_k]
                idx = idx[cut]
                val = val[cut]
            order = np.argsort(-val)
            yield int(src), idx[order], val[order]


# credits that say who made a work; companies that only publish, distribute
# or fund it are left out, and genre and language are deliberately unused
CONTENT_CREDIT_ROLES = (
    CreditRole.Author,
    CreditRole.Translator,
    CreditRole.Director,
    CreditRole.Playwright,
    CreditRole.Actor,
    CreditRole.VoiceActor,
    CreditRole.Artist,
    CreditRole.Designer,
    CreditRole.Composer,
    CreditRole.Choreographer,
    CreditRole.Performer,
    CreditRole.Host,
    CreditRole.OriginalCreator,
    CreditRole.Developer,
    CreditRole.Troupe,
)

# (source item, method, target items, scores) as yielded per category
Neighbours = tuple[int, int, np.ndarray, np.ndarray]
# (item after rewrite, feature key, weight)
FeatureCell = tuple[int, Hashable, float]


def _resolve_item(
    item_id: int, merged_to: int | None, rewrite: dict[int, int]
) -> int | None:
    """The item a row counts toward, or None for a merged item left out."""
    target = rewrite.get(item_id)
    if target is not None:
        return target
    return None if merged_to is not None else item_id


def _before(qs: QuerySet, field: str, until: datetime | None) -> QuerySet:
    return qs if until is None else qs.filter(**{f"{field}__lt": until})


def _tag_cells(
    ctypes: list[int],
    excluded_owners: set[int],
    rewrite: dict[int, int],
    until: datetime | None = None,
) -> Iterator[FeatureCell]:
    """Public tags; a cell weighs the number of owners who applied the tag.

    With ``until`` only tags applied before it count; the tag's visibility
    and title are as of now.
    """
    qs = TagMember.objects.filter(
        parent__visibility=0,
        item__polymorphic_ctype_id__in=ctypes,
        item__is_deleted=False,
    )
    qs = _before(qs, "created_time", until)
    if excluded_owners:
        qs = qs.exclude(owner_id__in=excluded_owners)
    rows = (
        qs.order_by()
        .values("item_id", "item__merged_to_item_id", "parent__title")
        .annotate(n=Count("id"))
        .values_list("item_id", "item__merged_to_item_id", "parent__title", "n")
        .iterator(chunk_size=20_000)
    )
    for item_id, merged_to, title, n in rows:
        key = Tag.deep_cleanup_title(title)
        target = _resolve_item(item_id, merged_to, rewrite)
        if key != "_" and target is not None:
            yield target, key, float(n)


def _collection_cells(
    ctypes: list[int],
    excluded_owners: set[int],
    rewrite: dict[int, int],
    until: datetime | None = None,
) -> Iterator[FeatureCell]:
    """Public collections; with ``until`` only items added before it count."""
    qs = CollectionMember.objects.filter(
        parent__visibility=0,
        item__polymorphic_ctype_id__in=ctypes,
        item__is_deleted=False,
    )
    qs = _before(qs, "created_time", until)
    if excluded_owners:
        qs = qs.exclude(owner_id__in=excluded_owners)
    rows = (
        qs.order_by()
        .values_list("item_id", "item__merged_to_item_id", "parent_id")
        .iterator(chunk_size=20_000)
    )
    for item_id, merged_to, collection_id in rows:
        target = _resolve_item(item_id, merged_to, rewrite)
        if target is not None:
            yield target, collection_id, 1.0


def _credit_cells(
    ctypes: list[int],
    excluded_owners: set[int],
    rewrite: dict[int, int],
    marked_targets: set[int],
    until: datetime | None = None,
) -> Iterator[FeatureCell]:
    """Creator credits of items with a public mark, which bounds the matrix.

    ``marked_targets`` adds the items such marks count toward, because a
    merge moves credits to the surviving item and leaves marks behind.
    ``until`` bounds the marks; credits are catalog data, read as of now.
    """
    marked = ShelfMember._base_manager.filter(
        visibility=0, parent__shelf_type__in=SHELF_TYPES_AS_SEED
    )
    marked = _before(marked, "edited_time", until)
    if excluded_owners:
        marked = marked.exclude(owner_id__in=excluded_owners)
    in_scope = Q(item_id__in=marked.order_by().values("item_id"))
    if marked_targets:
        in_scope |= Q(item_id__in=marked_targets)
    rows = (
        ItemCredit.objects.filter(
            in_scope,
            role__in=CONTENT_CREDIT_ROLES,
            item__polymorphic_ctype_id__in=ctypes,
            item__is_deleted=False,
        )
        .order_by()
        .values_list("item_id", "item__merged_to_item_id", "role", "person_id", "name")
        .iterator(chunk_size=20_000)
    )
    for item_id, merged_to, role, person_id, name in rows:
        target = _resolve_item(item_id, merged_to, rewrite)
        if target is None:
            continue
        if person_id is not None:
            yield target, (role, person_id), 1.0
        elif name and name.strip():
            yield target, (role, name.strip().casefold()), 1.0


def _unusable_items(
    item_ids: np.ndarray, excluded_ctypes: set[int], category_ctypes: list[int]
) -> tuple[set[int], set[int]]:
    """(not sources, not targets) among ``item_ids``, queried in chunks.

    An item outside ``category_ctypes`` is no source here: a merge can leave
    a cell on a survivor of another category, which has its own pass.
    """
    dead: set[int] = set()
    non_target: set[int] = set()
    q = Q(is_deleted=True) | Q(merged_to_item_id__isnull=False)
    if excluded_ctypes:
        q |= Q(polymorphic_ctype_id__in=excluded_ctypes)
    for i in range(0, len(item_ids), _ID_CHUNK):
        chunk = item_ids[i : i + _ID_CHUNK].tolist()
        for pk, deleted, merged_to in Item.objects.filter(q, pk__in=chunk).values_list(
            "pk", "is_deleted", "merged_to_item_id"
        ):
            non_target.add(pk)
            if deleted or merged_to is not None:
                dead.add(pk)
        dead.update(
            Item.objects.filter(pk__in=chunk)
            .exclude(polymorphic_ctype_id__in=category_ctypes)
            .values_list("pk", flat=True)
        )
    return dead, non_target


def _feature_topk(
    method: int,
    cells: Iterable[FeatureCell],
    binary: bool,
    top_k: int,
    max_feature_items: int,
    excluded_ctypes: set[int],
    category_ctypes: list[int],
) -> Iterator[Neighbours]:
    """Item-item cosine over shared features, for one category.

    Features on fewer than 2 or more than ``max_feature_items`` items are
    dropped, and each kept feature is damped by ``1/sqrt(n_items)`` so a
    common tag weighs less than a rare one. A cell sums its weights, or is 1
    when ``binary``. Every usable item with a kept feature is a source.
    """
    feature_ids: dict[Hashable, int] = {}
    f = array("i")
    it = array("q")
    w = array("f")
    for item_id, key, weight in cells:
        f.append(feature_ids.setdefault(key, len(feature_ids)))
        it.append(item_id)
        w.append(weight)
    del feature_ids
    if not f:
        return
    items, item_idx = np.unique(np.frombuffer(it, dtype=np.int64), return_inverse=True)
    pair = np.frombuffer(f, dtype=np.int32).astype(np.int64) * len(items) + item_idx
    pairs, pair_idx = np.unique(pair, return_inverse=True)
    if binary:
        weights = np.ones(len(pairs), dtype=np.float32)
    else:
        weights = np.bincount(
            pair_idx, weights=np.frombuffer(w, dtype=np.float32)
        ).astype(np.float32)
    del pair, pair_idx, item_idx, f, it, w
    feat = pairs // len(items)
    col = pairs % len(items)
    n_items = np.bincount(feat)
    keep = (n_items[feat] >= 2) & (n_items[feat] <= max_feature_items)
    if not keep.any():
        return
    feat, col, weights = feat[keep], col[keep], weights[keep]
    weights /= np.sqrt(n_items[feat]).astype(np.float32)
    kept_cols, cols = np.unique(col, return_inverse=True)
    item_ids = items[kept_cols]
    _, rows = np.unique(feat, return_inverse=True)
    m = csc_matrix(
        (weights, (rows.astype(np.int32), cols.astype(np.int32))),
        shape=(int(rows.max()) + 1, len(item_ids)),
    )
    dead, non_target = _unusable_items(item_ids, excluded_ctypes, category_ctypes)
    target_mask = ~np.isin(item_ids, np.fromiter(non_target, dtype=np.int64))
    sources = np.flatnonzero(~np.isin(item_ids, np.fromiter(dead, dtype=np.int64)))
    for src_col, tgt_cols, scores in _cosine_topk(m, sources, target_mask, top_k, 0.0):
        yield int(item_ids[src_col]), method, item_ids[tgt_cols], scores


class _BlendingWriter:
    """Blends each source's per-method neighbours into its top-K and keeps them.

    The blend is the weighted sum of a source's per-method scores per
    target, stored as ``METHOD_BLENDED``. Subclasses decide where it goes.
    """

    def __init__(self, blend_weights: dict[int, float], top_k: int) -> None:
        self.blend_weights = blend_weights
        self.top_k = top_k
        self.covered: set[int] = set()
        self.counts: dict[int, int] = defaultdict(int)

    def add(
        self, src: int, by_method: dict[int, tuple[np.ndarray, np.ndarray]]
    ) -> None:
        if src in self.covered:
            # a second add would delete the rows of the first
            raise RuntimeError(f"similarity source {src} added twice")
        blended: dict[int, float] = defaultdict(float)
        for method, (targets, scores) in by_method.items():
            self.counts[method] += len(targets)
            weight = self.blend_weights.get(method, 0.0)
            if not weight:
                continue
            for t, v in zip(targets.tolist(), scores.tolist()):
                blended[t] += weight * v
        top = nlargest(self.top_k, blended.items(), key=itemgetter(1))
        self.counts[ItemSimilarity.METHOD_BLENDED] += len(top)
        self.covered.add(src)
        self._keep(src, top)

    def _keep(self, src: int, top: list[tuple[int, float]]) -> None:
        raise NotImplementedError

    def finish(self) -> dict[int, int]:
        """Complete the build; return neighbour counts per method."""
        return dict(self.counts)


class InMemorySimilarity(_BlendingWriter):
    """Keeps the blended top-K per source in memory and touches no table.

    Called with source ids, it yields ``(source, target, score)`` like
    ``catalog.recommendation.stored_similarity``. Neighbours are kept in
    compact arrays: a site-wide build has as many sources as the weekly job.
    """

    def __init__(self, blend_weights: dict[int, float], top_k: int) -> None:
        super().__init__(blend_weights, top_k)
        self.neighbours: dict[int, tuple[array[int], array[float]]] = {}

    def _keep(self, src: int, top: list[tuple[int, float]]) -> None:
        if top:
            self.neighbours[src] = (
                array("q", (t for t, _ in top)),
                array("d", (v for _, v in top)),
            )

    def __call__(self, source_ids: Collection[int]) -> Iterator[tuple[int, int, float]]:
        for src in source_ids:
            found = self.neighbours.get(src)
            if found is not None:
                for t, v in zip(*found):
                    yield src, t, v


class _SimilarityWriter(_BlendingWriter):
    """Replaces every stored row of a source, in batches of sources.

    Batched transactions keep each commit small, avoid one long-lived
    transaction across the rebuild, and give readers a consistent view per
    source.
    """

    def __init__(self, blend_weights: dict[int, float], top_k: int) -> None:
        super().__init__(blend_weights, top_k)
        self.pending: dict[int, list[ItemSimilarity]] = {}

    def _keep(self, src: int, top: list[tuple[int, float]]) -> None:
        self.pending[src] = [
            ItemSimilarity(
                source_id=src,
                target_id=t,
                score=v,
                method=ItemSimilarity.METHOD_BLENDED,
            )
            for t, v in top
        ]
        if len(self.pending) >= _WRITE_SOURCE_BATCH:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        with transaction.atomic():
            ItemSimilarity.objects.filter(source_id__in=list(self.pending)).delete()
            ItemSimilarity.objects.bulk_create(
                [r for rows in self.pending.values() for r in rows],
                ignore_conflicts=True,
                batch_size=5_000,
            )
        self.pending = {}

    def finish(self) -> dict[int, int]:
        """Prune sources not covered by this run; return counts per method.

        ``flush`` deleted every row of each covered source whatever its
        method, and this deletes every row of the rest, so no row of another
        method survives a run, including those older builds stored.
        """
        self.flush()
        existing = set(
            ItemSimilarity.objects.order_by()
            .values_list("source_id", flat=True)
            .distinct()
        )
        stale = sorted(existing - self.covered)
        if stale:
            logger.info(f"Pruning {len(stale)} stale similarity sources")
        for i in range(0, len(stale), _ID_CHUNK):
            with transaction.atomic():
                ItemSimilarity.objects.filter(
                    source_id__in=stale[i : i + _ID_CHUNK]
                ).delete()
        return super().finish()


_Writer = TypeVar("_Writer", bound=_BlendingWriter)


def _enabled() -> bool:
    return bool(SiteConfig.system.enable_recommendations)


@JobManager.register
class BuildItemSimilarity(BaseJob):
    """Weekly item-item similarity builder.

    Per category it computes four methods in memory and stores only their
    weighted blend, as top-K ``METHOD_BLENDED`` rows per source. After
    deploying this, run ``manage.py recommendation similarity`` once: until
    then no blended rows exist and serving returns nothing.

    - shelf: cosine with shrinkage over weighted public marks of active
      items. Each mark carries ``mark_weight`` (rating and annotations) times
      ``1/sqrt(n_marks)`` (IDF damping, when enabled, to neutralise
      mega-shelvers); users are truncated to the most recent
      ``reco_user_mark_cap`` marks each.
    - tag, collection, credit: cosine over shared public tags, public
      collections and creator credits, for every item that has them.

    With ``until`` the build sees only marks, their ratings and annotations,
    tags and collection entries from before that time, for offline
    evaluation. Such a build is kept in memory by ``build_in_memory`` and
    never stored. Credits, merges and deletions are read as of now.
    """

    def __init__(self, until: datetime | None = None) -> None:
        self.until = until

    @classmethod
    def get_interval(cls) -> timedelta:
        if not _enabled():
            return timedelta(0)
        return timedelta(days=7)

    def _marks(self, qs: QuerySet) -> QuerySet:
        return _before(qs, "edited_time", self.until)

    def _rewrite_for_marked_items(self, rewrite: dict[int, int]) -> dict[int, int]:
        """Keep Productions and the merged items that still carry public marks.

        Merging moves marks to the surviving item, so few merged items keep
        any; trimming them keeps the ``IN`` lists below short.
        """
        if not rewrite:
            return rewrite
        marked_merged = set(
            self._marks(
                ShelfMember._base_manager.filter(
                    visibility=0,
                    parent__shelf_type__in=SHELF_TYPES_AS_SEED,
                    item__merged_to_item_id__isnull=False,
                )
            )
            .order_by()
            .values_list("item_id", flat=True)
            .distinct()
        )
        productions = production_to_performance_map()
        return {
            src: tgt
            for src, tgt in rewrite.items()
            if src in marked_merged or src in productions
        }

    def _marked_rewrite_targets(
        self, rewrite: dict[int, int], excluded_owners: set[int]
    ) -> set[int]:
        """Targets of rewrite sources that carry a public seed mark.

        ``rewrite`` is the trimmed map, so the ``IN`` list stays short.
        """
        if not rewrite:
            return set()
        qs = self._marks(
            ShelfMember._base_manager.filter(
                visibility=0,
                parent__shelf_type__in=SHELF_TYPES_AS_SEED,
                item_id__in=list(rewrite),
            )
        )
        if excluded_owners:
            qs = qs.exclude(owner_id__in=excluded_owners)
        return {
            rewrite[item_id]
            for item_id in qs.order_by().values_list("item_id", flat=True).distinct()
        }

    def _active_item_ids(
        self,
        min_marks: int,
        excluded_owners: set[int],
        rewrite: dict[int, int],
    ) -> set[int]:
        """Items with at least ``min_marks`` distinct owners after rewrite.

        Marks on a rewrite source count toward its target. Without rewrite
        this is a one-shot SQL aggregation; with rewrite we keep the fast SQL
        path for other items and stream the rewrite subset (typically small)
        to dedup owners against the target.
        """
        qs = self._marks(
            ShelfMember.objects.filter(
                visibility=0, parent__shelf_type__in=SHELF_TYPES_AS_SEED
            )
        )
        if excluded_owners:
            qs = qs.exclude(owner_id__in=excluded_owners)
        if not rewrite:
            return set(
                qs.values("item_id")
                .annotate(n=Count("id"))
                .filter(n__gte=min_marks)
                .values_list("item_id", flat=True)
            )

        rewrite_keys = set(rewrite)
        rewrite_targets = set(rewrite.values())

        counts: dict[int, int] = dict(
            qs.exclude(item_id__in=rewrite_keys)
            .values("item_id")
            .annotate(n=Count("id"))
            .values_list("item_id", "n")
        )

        # Per-target owner sets, seeded by direct marks on the target.
        target_owners: dict[int, set[int]] = {}
        for owner_id, item_id in (
            qs.filter(item_id__in=rewrite_targets)
            .values_list("owner_id", "item_id")
            .iterator(chunk_size=20_000)
        ):
            target_owners.setdefault(item_id, set()).add(owner_id)
        for owner_id, item_id in (
            qs.filter(item_id__in=rewrite_keys)
            .values_list("owner_id", "item_id")
            .iterator(chunk_size=20_000)
        ):
            target_owners.setdefault(rewrite[item_id], set()).add(owner_id)
        for target_id, owners in target_owners.items():
            counts[target_id] = len(owners)
        # rewrite sources are intentionally absent from `counts` (excluded above)
        return {iid for iid, c in counts.items() if c >= min_marks}

    def _user_item_pairs(
        self,
        active_items: set[int],
        cap: int,
        excluded_owners: set[int],
        rewrite: dict[int, int],
    ) -> dict[int, UserMarks]:
        """Per-user active item ids and mark weights, truncated to ``cap`` most recent.

        Streams ordered by (owner_id, -edited_time) so we can drop overflow per
        owner in-line without accumulating every mark in memory first. Critical
        at scale: a mega-shelver with 22k marks would otherwise allocate before
        being truncated. Rewrite sources are mapped to their targets and
        deduplicated per owner, keeping the most recent mark. Kept marks are
        weighed in batches of owners, see ``_weigh_marks``. A user with a
        single kept mark is kept too, because it counts toward cosine norms.
        """
        # rewrite sources whose target is active must be streamed too
        sources_to_include = (
            {src for src, tgt in rewrite.items() if tgt in active_items}
            if rewrite
            else set()
        )
        item_filter = active_items | sources_to_include

        qs = self._marks(
            ShelfMember._base_manager.filter(
                visibility=0,
                parent__shelf_type__in=SHELF_TYPES_AS_SEED,
                item_id__in=item_filter,
            )
        )
        if excluded_owners:
            qs = qs.exclude(owner_id__in=excluded_owners)
        qs = qs.order_by("owner_id", "-edited_time")
        rows = qs.values_list("owner_id", "item_id").iterator(chunk_size=20_000)

        out: dict[int, UserMarks] = {}
        pending: dict[int, list[int]] = {}
        current_owner: int | None = None
        current_items: list[int] = []
        current_seen: set[int] = set()
        for owner_id, item_id in rows:
            if owner_id != current_owner:
                if current_owner is not None and current_items:
                    pending[current_owner] = current_items
                    if len(pending) >= _WEIGH_OWNER_BATCH:
                        out.update(self._weigh_marks(pending, rewrite))
                        pending = {}
                current_owner = owner_id
                current_items = []
                current_seen = set()
            mapped = rewrite.get(item_id, item_id) if rewrite else item_id
            if mapped in current_seen:
                continue
            if len(current_items) < cap:
                current_items.append(mapped)
                current_seen.add(mapped)
        if current_owner is not None and current_items:
            pending[current_owner] = current_items
        if pending:
            out.update(self._weigh_marks(pending, rewrite))
        return out

    def _weigh_marks(
        self, pending: dict[int, list[int]], rewrite: dict[int, int]
    ) -> dict[int, UserMarks]:
        """Attach a ``mark_weight`` to each kept mark of a batch of owners.

        The rating is centered on the owner's mean over the same kept marks.
        """
        sys = SiteConfig.system
        signals = load_mark_signals(
            {owner_id: set(items) for owner_id, items in pending.items()},
            rewrite,
            until=self.until,
        )
        out: dict[int, UserMarks] = {}
        for owner_id, items in pending.items():
            owner_signals = [
                signals.get((owner_id, it), NO_MARK_SIGNALS) for it in items
            ]
            user_mean = user_mean_grade(g for g, _, _, _ in owner_signals)
            weights = array(
                "f",
                (
                    reco_mark_weight(
                        sys,
                        grade,
                        user_mean,
                        has_comment=has_comment,
                        has_review=has_review,
                        has_note=has_note,
                    )
                    for grade, has_comment, has_review, has_note in owner_signals
                ),
            )
            out[owner_id] = UserMarks(array("q", items), weights)
        return out

    def run(self) -> None:
        if self.until is not None:
            raise ValueError("a time-bounded build must not replace stored rows")
        self._build(_SimilarityWriter)

    def build_in_memory(self) -> InMemorySimilarity:
        """Build without writing, for ``compute_for_user(similarity=...)``."""
        return self._build(InMemorySimilarity)

    def _build(self, writer_class: type[_Writer]) -> _Writer:
        sys = SiteConfig.system
        min_source = sys.reco_min_source_marks
        min_target = sys.reco_min_target_marks
        cap = sys.reco_user_mark_cap
        top_k = sys.reco_similarity_top_k
        dampen = sys.reco_user_idf_dampen
        shrinkage = sys.reco_similarity_shrinkage
        max_feature_items = sys.reco_max_feature_items
        excluded = _non_discoverable_identity_ids()
        full_rewrite = training_rewrite_map()
        rewrite = self._rewrite_for_marked_items(full_rewrite)
        marked_targets = self._marked_rewrite_targets(rewrite, excluded)
        excluded_target_ctypes = excluded_target_ctype_ids()
        logger.info(
            f"Similarity build start: min_source={min_source} min_target={min_target} "
            f"cap={cap} top_k={top_k} dampen={dampen} shrinkage={shrinkage} "
            f"excluded_owners={len(excluded)} "
            f"rewrites={len(rewrite)} excluded_target_ctypes={len(excluded_target_ctypes)}"
        )

        active = self._active_item_ids(min_source, excluded, rewrite)
        target_set = (
            self._active_item_ids(min_target, excluded, rewrite)
            if min_target < min_source
            else active
        )
        # Remove classes that should never be recommendation targets, even if
        # they otherwise meet the threshold (Production marks have already been
        # rewritten upstream, so PerformanceProductions never appear here).
        if excluded_target_ctypes:
            excluded_target_ids = set(
                Item.objects.filter(
                    pk__in=target_set,
                    polymorphic_ctype_id__in=excluded_target_ctypes,
                ).values_list("pk", flat=True)
            )
            target_set = target_set - excluded_target_ids

        # Resolve item -> category once, dropping deleted items and merged
        # items left out of the rewrite (their chain ends on a deleted item).
        # Every method works per category, which bounds its matrices.
        # Several content types share the same category string (TVShow /
        # TVSeason / TVEpisode -> "tv"), so we use the category label rather
        # than polymorphic_ctype_id to avoid blocking cross-type pairs within
        # a category.
        ctype_to_cat: dict[int, str] = {}
        cts = item_content_types()
        for cat_enum, classes in item_categories().items():
            for cls in classes:
                ct_id = cts.get(cls)
                if ct_id is not None:
                    ctype_to_cat[ct_id] = str(cat_enum)
        live: set[int] = set()
        items_by_cat: dict[str, set[int]] = defaultdict(set)
        for pk, ct_id in Item.objects.filter(
            pk__in=active | target_set,
            is_deleted=False,
            merged_to_item_id__isnull=True,
        ).values_list("pk", "polymorphic_ctype_id"):
            live.add(pk)
            cat = ctype_to_cat.get(ct_id)
            if cat:
                items_by_cat[cat].add(pk)
        active &= live
        target_set &= live
        logger.info(f"Active items: {len(active)} target candidates: {len(target_set)}")

        category_by_id = {
            iid: cat for cat, cat_items in items_by_cat.items() for iid in cat_items
        }
        users_by_cat: dict[str, list[UserMarks]] = defaultdict(list)
        if active:
            user_items = self._user_item_pairs(
                active | target_set, cap, excluded, rewrite
            )
            logger.info(f"Users contributing: {len(user_items)}")
            # Bucket each user's truncated mark list by the categories it
            # touches, so a category only walks users with an item in it.
            for marks in user_items.values():
                cats_in_user: set[str] = set()
                for it in marks.items:
                    cat = category_by_id.get(it)
                    if cat is not None:
                        cats_in_user.add(cat)
                for cat in cats_in_user:
                    users_by_cat[cat].append(marks)
        del category_by_id

        ctypes_by_cat: dict[str, list[int]] = defaultdict(list)
        for ct_id, cat in ctype_to_cat.items():
            ctypes_by_cat[cat].append(ct_id)
        writer = writer_class(
            {
                ItemSimilarity.METHOD_SHELF_COOC: 1.0,
                ItemSimilarity.METHOD_TAG_COOC: sys.reco_tag_weight,
                ItemSimilarity.METHOD_COLLECTION_COOC: sys.reco_collection_weight,
                ItemSimilarity.METHOD_CONTENT: sys.reco_content_weight,
            },
            top_k,
        )
        if top_k > 0:
            for cat, ctypes in ctypes_by_cat.items():
                cat_items = items_by_cat.get(cat, set())
                methods = [
                    self._shelf_topk(
                        users_by_cat[cat],
                        cat_items,
                        active & cat_items,
                        target_set & cat_items,
                        top_k,
                        dampen,
                        shrinkage,
                    ),
                    _feature_topk(
                        ItemSimilarity.METHOD_TAG_COOC,
                        _tag_cells(ctypes, excluded, full_rewrite, self.until),
                        False,
                        top_k,
                        max_feature_items,
                        excluded_target_ctypes,
                        ctypes,
                    ),
                    _feature_topk(
                        ItemSimilarity.METHOD_COLLECTION_COOC,
                        _collection_cells(ctypes, excluded, full_rewrite, self.until),
                        True,
                        top_k,
                        max_feature_items,
                        excluded_target_ctypes,
                        ctypes,
                    ),
                    _feature_topk(
                        ItemSimilarity.METHOD_CONTENT,
                        _credit_cells(
                            ctypes,
                            excluded,
                            full_rewrite,
                            marked_targets,
                            self.until,
                        ),
                        True,
                        top_k,
                        max_feature_items,
                        excluded_target_ctypes,
                        ctypes,
                    ),
                ]
                # every method must yield each source once, in ascending item
                # id order, so one pass groups each source's rows across
                # methods; the writer refuses a source seen twice
                for src, group in groupby(
                    merge(*methods, key=itemgetter(0)), key=itemgetter(0)
                ):
                    writer.add(src, {m: (t, v) for _, m, t, v in group})
        counts = writer.finish()
        logger.info(
            f"Similarity build done: {len(writer.covered)} sources, neighbours "
            f"per method {counts}"
        )
        return writer

    def _shelf_topk(
        self,
        users: list[UserMarks],
        cat_items: set[int],
        active_in_cat: set[int],
        target_in_cat: set[int],
        top_k: int,
        dampen: bool,
        shrinkage: float,
    ) -> Iterator[Neighbours]:
        """Shelf cosine similarity for one category: top-K per active source.

        Builds a ``scipy.sparse`` user-item matrix. Each entry is
        ``w_u * mark_weight``, where ``w_u = 1/sqrt(n_user_total)`` (or 1 when
        damping is off). Scores are cosine times ``n / (n + shrinkage)`` with
        ``n`` the users who marked both items, see ``_cosine_topk``. Users
        with a single mark in the category add no pair but still count toward
        the item norms, as cosine requires.
        """
        if not active_in_cat or not target_in_cat:
            return
        item_list = sorted(cat_items)
        col_of = {iid: idx for idx, iid in enumerate(item_list)}
        n_items = len(item_list)

        rows = array("i")
        cols = array("i")
        data = array("f")
        user_idx = 0
        for marks in users:
            # Damping uses the user's full truncated list size, not the
            # per-category subset, so a heavy shelver is damped equally
            # regardless of which category we're currently scoring.
            n_total = len(marks.items)
            w = (1.0 / math.sqrt(n_total)) if dampen else 1.0
            for it, mw in zip(marks.items, marks.weights):
                col = col_of.get(it)
                if col is None:
                    continue
                rows.append(user_idx)
                cols.append(col)
                data.append(w * mw)
            user_idx += 1
        if user_idx == 0:
            return

        m = _coo_to_csc(rows, cols, data, (user_idx, n_items))
        del rows, cols, data
        target_mask = np.zeros(n_items, dtype=bool)
        for iid in target_in_cat:
            target_mask[col_of[iid]] = True
        sources = np.array(sorted(col_of[i] for i in active_in_cat))
        item_arr = np.asarray(item_list, dtype=np.int64)
        for src_col, tgt_cols, scores in _cosine_topk(
            m, sources, target_mask, top_k, shrinkage
        ):
            yield (
                item_list[src_col],
                ItemSimilarity.METHOD_SHELF_COOC,
                item_arr[tgt_cols],
                scores,
            )


@JobManager.register
class BuildUserRecommendations(BaseJob):
    """Nightly per-user personalised recommendations.

    Refreshes only users with at least one mark, of any visibility, in the
    last ``reco_user_active_days`` days. Cold users get on-demand compute via
    ``catalog.recommendation.for_you`` at request time, which also serves
    ``blended_for_discover``; ``from_your_circles`` is never stored.
    """

    @classmethod
    def get_interval(cls) -> timedelta:
        if not _enabled():
            return timedelta(0)
        return timedelta(days=1)

    def _active_users(self, days: int) -> list[int]:
        since = timezone.now() - timedelta(days=days)
        return list(
            ShelfMember.objects.filter(edited_time__gte=since)
            .values_list("owner_id", flat=True)
            .distinct()
        )

    def _user_pk_by_identity(self, identity_ids: list[int]) -> dict[int, int]:
        """Map identity_pk -> user_pk for local identities only.

        Remote identities have ``user_id`` null; including them would cause
        a NOT NULL violation in ``UserRecommendation.user_id`` and abort the
        whole nightly refresh.
        """
        return dict(
            APIdentity.objects.filter(
                pk__in=identity_ids, user_id__isnull=False
            ).values_list("pk", "user_id")
        )

    def run(self) -> None:
        sys = SiteConfig.system
        active_days = sys.reco_user_active_days
        identities = self._active_users(active_days)
        if not identities:
            logger.info("No active users in window; nothing to refresh")
            return
        user_by_identity = self._user_pk_by_identity(identities)
        logger.info(
            f"Refreshing recommendations for {len(user_by_identity)} active users"
        )

        # Per-user atomic replace: each user's refresh is independent, so a
        # transaction-per-user keeps each commit small and bounds rollback
        # blast radius if any single user's compute fails.
        built = 0
        for identity_pk, user_pk in user_by_identity.items():
            try:
                rows = compute_for_user(user_pk, identity_pk)
            except Exception as e:
                logger.exception(f"compute_for_user failed for user {user_pk}: {e}")
                continue
            with transaction.atomic():
                UserRecommendation.objects.filter(user_id=user_pk).delete()
                if rows:
                    UserRecommendation.objects.bulk_create(rows, ignore_conflicts=True)
            built += len(rows)
        logger.info(
            f"User recommendations done: {built} rows across {len(user_by_identity)} users"
        )
