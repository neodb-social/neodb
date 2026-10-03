import logging
import math
from array import array
from collections import defaultdict
from collections.abc import Iterator
from datetime import timedelta
from typing import NamedTuple

import numpy as np
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from scipy.sparse import csc_matrix, csr_matrix

from catalog.models import (
    Item,
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
from journal.models import ShelfMember
from takahe.models import Identity as TakaheIdentity
from users.models import APIdentity

logger = logging.getLogger(__name__)

# owners whose kept marks are weighed together; bounds the signal lookup
_WEIGH_OWNER_BATCH = 200
# source columns per item-item product block, bounds peak memory
_SOURCE_BLOCK = 256


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


def _enabled() -> bool:
    return bool(SiteConfig.system.enable_recommendations)


@JobManager.register
class BuildItemSimilarity(BaseJob):
    """Weekly item-item shelf co-occurrence builder.

    Output: top-K rows in ``ItemSimilarity`` per active source item, scored by
    cosine similarity with shrinkage over weighted marks. Each mark carries
    ``mark_weight`` (rating and annotations) times ``1/sqrt(n_marks)`` (IDF
    damping, when enabled, to neutralise mega-shelvers); users are truncated
    to the most recent ``reco_user_mark_cap`` marks each.
    """

    @classmethod
    def get_interval(cls) -> timedelta:
        if not _enabled():
            return timedelta(0)
        return timedelta(days=7)

    def _rewrite_for_marked_items(self, rewrite: dict[int, int]) -> dict[int, int]:
        """Keep Productions and the merged items that still carry public marks.

        Merging moves marks to the surviving item, so few merged items keep
        any; trimming them keeps the ``IN`` lists below short.
        """
        if not rewrite:
            return rewrite
        marked_merged = set(
            ShelfMember._base_manager.filter(
                visibility=0,
                parent__shelf_type__in=SHELF_TYPES_AS_SEED,
                item__merged_to_item_id__isnull=False,
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
        qs = ShelfMember.objects.filter(
            visibility=0, parent__shelf_type__in=SHELF_TYPES_AS_SEED
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

        qs = ShelfMember._base_manager.filter(
            visibility=0,
            parent__shelf_type__in=SHELF_TYPES_AS_SEED,
            item_id__in=item_filter,
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
            {owner_id: set(items) for owner_id, items in pending.items()}, rewrite
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
        sys = SiteConfig.system
        min_source = sys.reco_min_source_marks
        min_target = sys.reco_min_target_marks
        cap = sys.reco_user_mark_cap
        top_k = sys.reco_similarity_top_k
        dampen = sys.reco_user_idf_dampen
        shrinkage = sys.reco_similarity_shrinkage
        excluded = _non_discoverable_identity_ids()
        rewrite = self._rewrite_for_marked_items(training_rewrite_map())
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
        category_by_id: dict[int, str] = {}
        for pk, ct_id in Item.objects.filter(
            pk__in=active | target_set,
            is_deleted=False,
            merged_to_item_id__isnull=True,
        ).values_list("pk", "polymorphic_ctype_id"):
            live.add(pk)
            cat = ctype_to_cat.get(ct_id)
            if cat:
                category_by_id[pk] = cat
        active &= live
        target_set &= live
        logger.info(f"Active items: {len(active)} target candidates: {len(target_set)}")

        item_categories_map: dict[int, list[tuple[int, float]]] = {}
        if active:
            user_items = self._user_item_pairs(
                active | target_set, cap, excluded, rewrite
            )
            logger.info(f"Users contributing: {len(user_items)}")

            item_categories_map = self._topk_per_category(
                user_items=user_items,
                active=active,
                target_set=target_set,
                category_by_id=category_by_id,
                top_k=top_k,
                dampen=dampen,
                shrinkage=shrinkage,
            )

        logger.info(f"Sources with similar rows: {len(item_categories_map)}")
        rows_written = self._write_similarity_rows(item_categories_map)
        logger.info(f"Similarity build done: {rows_written} rows")

    def _topk_per_category(
        self,
        user_items: dict[int, UserMarks],
        active: set[int],
        target_set: set[int],
        category_by_id: dict[int, str],
        top_k: int,
        dampen: bool,
        shrinkage: float,
    ) -> dict[int, list[tuple[int, float]]]:
        """Per-category sparse cosine similarity: top-K targets per active source.

        Builds one ``scipy.sparse`` user-item matrix per category. Each entry
        is ``w_u * mark_weight``, where ``w_u = 1/sqrt(n_user_total)`` (or 1
        when damping is off). Scores are cosine times ``n / (n + shrinkage)``
        with ``n`` the users who marked both items, see ``_cosine_topk``.
        Users with a single mark in the category add no pair but still count
        toward the item norms, as cosine requires.
        """
        if top_k <= 0:
            return {}
        items_by_cat: dict[str, set[int]] = defaultdict(set)
        for iid, cat in category_by_id.items():
            items_by_cat[cat].add(iid)

        # Bucket each user's truncated mark list by the categories it touches,
        # so the per-category inner loop only walks users with at least one
        # item in that category instead of every user every time. Users who
        # specialise in 1-2 categories are the common case.
        users_by_cat: dict[str, list[UserMarks]] = defaultdict(list)
        for marks in user_items.values():
            cats_in_user: set[str] = set()
            for it in marks.items:
                cat = category_by_id.get(it)
                if cat is not None:
                    cats_in_user.add(cat)
            for cat in cats_in_user:
                users_by_cat[cat].append(marks)

        out: dict[int, list[tuple[int, float]]] = {}
        for cat, cat_items in items_by_cat.items():
            active_in_cat = active & cat_items
            target_in_cat = target_set & cat_items
            if not active_in_cat or not target_in_cat:
                continue
            item_list = sorted(cat_items)
            col_of = {iid: idx for idx, iid in enumerate(item_list)}
            n_items = len(item_list)

            rows = array("i")
            cols = array("i")
            data = array("f")
            user_idx = 0
            for marks in users_by_cat[cat]:
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
                continue

            m = _coo_to_csc(rows, cols, data, (user_idx, n_items))
            del rows, cols, data

            target_mask = np.zeros(n_items, dtype=bool)
            for iid in target_in_cat:
                target_mask[col_of[iid]] = True
            sources = np.array(sorted(col_of[i] for i in active_in_cat))
            for src_col, tgt_cols, scores in _cosine_topk(
                m, sources, target_mask, top_k, shrinkage
            ):
                out[item_list[src_col]] = [
                    (item_list[c], float(v)) for c, v in zip(tgt_cols, scores)
                ]
        return out

    def _write_similarity_rows(
        self, item_categories_map: dict[int, list[tuple[int, float]]]
    ) -> int:
        """Replace shelf-cooc rows per source in short atomic batches.

        Per-source transactions keep each commit small (<= top_k rows), avoid
        holding a long-lived DB transaction across the whole rebuild, and
        present a consistent per-source view to concurrent readers during the
        run. Sources no longer covered are cleaned up afterwards in chunks.
        """
        rows_written = 0
        covered: set[int] = set()
        for src, top in item_categories_map.items():
            covered.add(src)
            new_rows = [
                ItemSimilarity(
                    source_id=src,
                    target_id=tgt,
                    score=score,
                    method=ItemSimilarity.METHOD_SHELF_COOC,
                )
                for tgt, score in top
            ]
            with transaction.atomic():
                ItemSimilarity.objects.filter(
                    source_id=src, method=ItemSimilarity.METHOD_SHELF_COOC
                ).delete()
                if new_rows:
                    ItemSimilarity.objects.bulk_create(new_rows, ignore_conflicts=True)
            rows_written += len(new_rows)
        # Drop orphan rows for sources that no longer meet thresholds. Chunk to
        # avoid a single very large DELETE on a populated table.
        stale_ids = list(
            ItemSimilarity.objects.filter(method=ItemSimilarity.METHOD_SHELF_COOC)
            .exclude(source_id__in=covered)
            .values_list("source_id", flat=True)
            .distinct()
        )
        if stale_ids:
            logger.info(f"Pruning {len(stale_ids)} stale similarity sources")
            for i in range(0, len(stale_ids), 1000):
                chunk = stale_ids[i : i + 1000]
                with transaction.atomic():
                    ItemSimilarity.objects.filter(
                        method=ItemSimilarity.METHOD_SHELF_COOC,
                        source_id__in=chunk,
                    ).delete()
        return rows_written


@JobManager.register
class BuildUserRecommendations(BaseJob):
    """Nightly per-user personalised recommendations.

    Refreshes only users with at least one public mark in the last
    ``reco_user_active_days`` days. Cold users get on-demand compute via
    ``catalog.recommendation.recommendations_for`` at request time.
    """

    @classmethod
    def get_interval(cls) -> timedelta:
        if not _enabled():
            return timedelta(0)
        return timedelta(days=1)

    def _active_users(self, days: int) -> list[int]:
        since = timezone.now() - timedelta(days=days)
        return list(
            ShelfMember.objects.filter(visibility=0, edited_time__gte=since)
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
