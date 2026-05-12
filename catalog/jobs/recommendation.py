import math
from collections import defaultdict
from datetime import timedelta
from heapq import nlargest

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from loguru import logger

from catalog.models import (
    Item,
    ItemSimilarity,
    UserRecommendation,
    item_categories,
    item_content_types,
)
from catalog.recommendation import (
    SHELF_TYPES_AS_SEED,
    compute_for_user,
    excluded_target_ctype_ids,
    production_to_performance_map,
)
from common.models import BaseJob, JobManager, SiteConfig
from journal.models import ShelfMember
from takahe.models import Identity as TakaheIdentity
from users.models import APIdentity


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


def _enabled() -> bool:
    return bool(SiteConfig.system.enable_recommendations)


@JobManager.register
class BuildItemSimilarity(BaseJob):
    """Weekly item-item shelf co-occurrence builder.

    Output: top-K rows in ``ItemSimilarity`` per active source item, scored by
    cosine-like sum of per-user weights. Users contribute ``1/sqrt(n_marks)``
    (IDF damping) when enabled to neutralise mega-shelvers, and are truncated
    to the most recent ``reco_user_mark_cap`` marks each.
    """

    @classmethod
    def get_interval(cls) -> timedelta:
        if not _enabled():
            return timedelta(0)
        return timedelta(days=7)

    def _active_item_ids(
        self,
        min_marks: int,
        excluded_owners: set[int],
        rewrite: dict[int, int],
    ) -> set[int]:
        """Items with at least ``min_marks`` distinct owners after rewrite.

        Marks on Productions count toward their parent Performance. Without
        rewrite this is a one-shot SQL aggregation; with rewrite we keep the
        fast SQL path for non-Production items and stream the Production
        subset (typically small) to dedup owners against the parent.
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

        # Build per-Performance owner sets, seeded by direct Performance marks.
        perf_owners: dict[int, set[int]] = {}
        for owner_id, item_id in (
            qs.filter(item_id__in=rewrite_targets)
            .values_list("owner_id", "item_id")
            .iterator(chunk_size=20_000)
        ):
            perf_owners.setdefault(item_id, set()).add(owner_id)
        # Add Production marks rewritten to their Performance.
        for owner_id, item_id in (
            qs.filter(item_id__in=rewrite_keys)
            .values_list("owner_id", "item_id")
            .iterator(chunk_size=20_000)
        ):
            mapped = rewrite[item_id]
            perf_owners.setdefault(mapped, set()).add(owner_id)
        # Replace direct counts with the deduped Performance total.
        for perf_id, owners in perf_owners.items():
            counts[perf_id] = len(owners)
        # Productions are intentionally absent from `counts` (excluded above).
        return {iid for iid, c in counts.items() if c >= min_marks}

    def _user_item_pairs(
        self,
        active_items: set[int],
        cap: int,
        excluded_owners: set[int],
        rewrite: dict[int, int],
    ) -> dict[int, list[int]]:
        """Per-user lists of active item ids, each truncated to ``cap`` most recent.

        Streams ordered by (owner_id, -edited_time) so we can drop overflow per
        owner in-line without accumulating every mark in memory first. Critical
        at scale: a mega-shelver with 22k marks would otherwise allocate before
        being truncated. Production marks are rewritten to Performance ids and
        deduplicated per owner.
        """
        # Production ids whose Performance is in active_items also need to be
        # streamed (so they can rewrite into the active set).
        prod_ids_to_include = (
            {pid for pid, perf in rewrite.items() if perf in active_items}
            if rewrite
            else set()
        )
        item_filter = active_items | prod_ids_to_include

        qs = ShelfMember.objects.filter(
            visibility=0,
            parent__shelf_type__in=SHELF_TYPES_AS_SEED,
            item_id__in=item_filter,
        )
        if excluded_owners:
            qs = qs.exclude(owner_id__in=excluded_owners)
        qs = qs.order_by("owner_id", "-edited_time")
        rows = qs.values_list("owner_id", "item_id").iterator(chunk_size=20_000)

        out: dict[int, list[int]] = {}
        current_owner: int | None = None
        current_items: list[int] = []
        current_seen: set[int] = set()
        for owner_id, item_id in rows:
            if owner_id != current_owner:
                if current_owner is not None and len(current_items) >= 2:
                    out[current_owner] = current_items
                current_owner = owner_id
                current_items = []
                current_seen = set()
            mapped = rewrite.get(item_id, item_id) if rewrite else item_id
            if mapped in current_seen:
                continue
            if len(current_items) < cap:
                current_items.append(mapped)
                current_seen.add(mapped)
        if current_owner is not None and len(current_items) >= 2:
            out[current_owner] = current_items
        return out

    def run(self) -> None:
        sys = SiteConfig.system
        min_source = sys.reco_min_source_marks
        min_target = sys.reco_min_target_marks
        cap = sys.reco_user_mark_cap
        top_k = sys.reco_similarity_top_k
        dampen = sys.reco_user_idf_dampen
        excluded = _non_discoverable_identity_ids()
        rewrite = production_to_performance_map()
        excluded_target_ctypes = excluded_target_ctype_ids()
        logger.info(
            f"Similarity build start: min_source={min_source} min_target={min_target} "
            f"cap={cap} top_k={top_k} dampen={dampen} excluded_owners={len(excluded)} "
            f"production_rewrites={len(rewrite)} excluded_target_ctypes={len(excluded_target_ctypes)}"
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
        logger.info(f"Active items: {len(active)} target candidates: {len(target_set)}")

        item_categories_map: dict[int, list[tuple[int, float]]] = {}
        if active:
            user_items = self._user_item_pairs(
                active | target_set, cap, excluded, rewrite
            )
            logger.info(f"Users contributing: {len(user_items)}")

            # Co-occurrence scores live in a nested dict. With cap+IDF damping
            # the unique-pair count is bounded by the top-K output size; at
            # ~13M output rows the peak in-memory cost is ~1-2 GB on a Python
            # worker, tolerable for a weekly job. If catalog growth pushes
            # this past worker memory, partition `active` and run per-chunk.
            scores: dict[int, dict[int, float]] = defaultdict(
                lambda: defaultdict(float)
            )
            for items in user_items.values():
                n = len(items)
                if n < 2:
                    continue
                w = (1.0 / math.sqrt(n)) if dampen else 1.0
                ws = w * w
                for i in range(n):
                    a = items[i]
                    row = scores[a]
                    for j in range(i + 1, n):
                        b = items[j]
                        row[b] += ws
                        scores[b][a] += ws

            # Group via Item.category (string), not polymorphic_ctype_id.
            # Several content types map to the same category (e.g. TVShow,
            # TVSeason and TVEpisode all live under "tv"); using ctype_id
            # would block cross-type recommendations within a category.
            ctype_to_cat: dict[int, str] = {}
            cts = item_content_types()
            for cat_enum, classes in item_categories().items():
                for cls in classes:
                    ct_id = cts.get(cls)
                    if ct_id is not None:
                        ctype_to_cat[ct_id] = str(cat_enum)
            category_by_id: dict[int, str] = {}
            for pk, ct_id in Item.objects.filter(
                pk__in=active | target_set
            ).values_list("pk", "polymorphic_ctype_id"):
                cat = ctype_to_cat.get(ct_id)
                if cat:
                    category_by_id[pk] = cat

            for src in active:
                row = scores.get(src)
                if not row:
                    continue
                src_cat = category_by_id.get(src)
                if not src_cat:
                    continue
                same_cat = [
                    (b, s)
                    for b, s in row.items()
                    if b in target_set and category_by_id.get(b) == src_cat
                ]
                if not same_cat:
                    continue
                top = nlargest(top_k, same_cat, key=lambda t: t[1])
                item_categories_map[src] = top

        logger.info(f"Sources with similar rows: {len(item_categories_map)}")
        rows_written = self._write_similarity_rows(item_categories_map)
        logger.info(f"Similarity build done: {rows_written} rows")

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
