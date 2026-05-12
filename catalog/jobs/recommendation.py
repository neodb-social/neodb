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
)
from common.models import BaseJob, JobManager, SiteConfig
from journal.models import ShelfMember
from takahe.models import Identity as TakaheIdentity

PROGRESS_COMPLETE = ["progress", "complete"]


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

    def _active_item_ids(self, min_marks: int, excluded_owners: set[int]) -> set[int]:
        qs = ShelfMember.objects.filter(
            visibility=0, parent__shelf_type__in=PROGRESS_COMPLETE
        )
        if excluded_owners:
            qs = qs.exclude(owner_id__in=excluded_owners)
        rows = (
            qs.values("item_id")
            .annotate(n=Count("id"))
            .filter(n__gte=min_marks)
            .values_list("item_id", flat=True)
        )
        return set(rows)

    def _user_item_pairs(
        self, active_items: set[int], cap: int, excluded_owners: set[int]
    ) -> dict[int, list[int]]:
        """Per-user lists of active item ids, each truncated to ``cap`` most recent."""
        pairs: dict[int, list[tuple[int, int]]] = defaultdict(list)
        qs = ShelfMember.objects.filter(
            visibility=0,
            parent__shelf_type__in=PROGRESS_COMPLETE,
            item_id__in=active_items,
        )
        if excluded_owners:
            qs = qs.exclude(owner_id__in=excluded_owners)
        rows = qs.values_list("owner_id", "item_id", "edited_time").iterator(
            chunk_size=20_000
        )
        for owner_id, item_id, edited in rows:
            ts = int(edited.timestamp()) if edited else 0
            pairs[owner_id].append((ts, item_id))
        out: dict[int, list[int]] = {}
        for owner_id, lst in pairs.items():
            if len(lst) > cap:
                lst.sort(reverse=True)
                lst = lst[:cap]
            out[owner_id] = [iid for _, iid in lst]
        return out

    def run(self) -> None:
        sys = SiteConfig.system
        min_source = sys.reco_min_source_marks
        min_target = sys.reco_min_target_marks
        cap = sys.reco_user_mark_cap
        top_k = sys.reco_similarity_top_k
        dampen = sys.reco_user_idf_dampen
        excluded = _non_discoverable_identity_ids()
        logger.info(
            f"Similarity build start: min_source={min_source} min_target={min_target} "
            f"cap={cap} top_k={top_k} dampen={dampen} excluded_owners={len(excluded)}"
        )

        active = self._active_item_ids(min_source, excluded)
        if not active:
            logger.info("No active items meet threshold; aborting")
            return
        target_set = (
            self._active_item_ids(min_target, excluded)
            if min_target < min_source
            else active
        )
        logger.info(f"Active items: {len(active)} target candidates: {len(target_set)}")

        user_items = self._user_item_pairs(active | target_set, cap, excluded)
        logger.info(f"Users contributing: {len(user_items)}")

        scores: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
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

        category_by_id = dict(
            Item.objects.filter(pk__in=active).values_list("pk", "polymorphic_ctype_id")
        )

        rows_written = 0
        item_categories_map = {}
        for src in active:
            row = scores.get(src)
            if not row:
                continue
            src_ct = category_by_id.get(src)
            same_cat = [
                (b, s)
                for b, s in row.items()
                if b in target_set and category_by_id.get(b) == src_ct
            ]
            if not same_cat:
                continue
            top = nlargest(top_k, same_cat, key=lambda t: t[1])
            item_categories_map[src] = top

        logger.info(f"Sources with similar rows: {len(item_categories_map)}")
        with transaction.atomic():
            ItemSimilarity.objects.filter(
                method=ItemSimilarity.METHOD_SHELF_COOC
            ).delete()
            batch: list[ItemSimilarity] = []
            for src, top in item_categories_map.items():
                for tgt, score in top:
                    batch.append(
                        ItemSimilarity(
                            source_id=src,
                            target_id=tgt,
                            score=score,
                            method=ItemSimilarity.METHOD_SHELF_COOC,
                        )
                    )
                    if len(batch) >= 5000:
                        ItemSimilarity.objects.bulk_create(batch, ignore_conflicts=True)
                        rows_written += len(batch)
                        batch = []
            if batch:
                ItemSimilarity.objects.bulk_create(batch, ignore_conflicts=True)
                rows_written += len(batch)
        logger.info(f"Similarity build done: {rows_written} rows")


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
        from users.models import APIdentity

        return dict(
            APIdentity.objects.filter(pk__in=identity_ids).values_list("pk", "user_id")
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
        from catalog.recommendation import compute_for_user

        with transaction.atomic():
            UserRecommendation.objects.filter(
                user_id__in=user_by_identity.values()
            ).delete()
            built = 0
            for identity_pk, user_pk in user_by_identity.items():
                rows = compute_for_user(user_pk, identity_pk)
                if not rows:
                    continue
                UserRecommendation.objects.bulk_create(rows, ignore_conflicts=True)
                built += len(rows)
        logger.info(
            f"User recommendations done: {built} rows across {len(user_by_identity)} users"
        )
