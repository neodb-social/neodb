"""Whole-instance results the annual summary cards compare against: the instance totals and the kindred matches.

Both are computed by jobs and cached; a page or a deck update reads the cache.
"""

import logging
import math
import statistics
import time
from collections import defaultdict
from typing import Any
from zoneinfo import ZoneInfo

import django_rq
from django.conf import settings
from django.core.cache import cache
from django.db.models import (
    Count,
    Q,
)
from django.db.models.functions import TruncDate

from catalog.models import (
    Item,
)
from takahe.models import Identity as TakaheIdentity
from users.models import APIdentity

from journal.models import (
    ShelfType,
)

from .base import (
    BULK_DAY,
    _card_item,
    _members,
    year_range,
)

logger = logging.getLogger(__name__)


INSTANCE_CACHE_SECONDS = 24 * 3600


# the kindred card: a member the owner does not follow whose public year overlaps the owner's the most
KINDRED_MIN_SHARED = 5


# titles more members finished than this are left out: they say little about taste and cost the most
# (2026 on neodb.social: 6 of 105,248 titles, by a read-only count on 2026-11-27)
KINDRED_ITEM_CAP = 300


KINDRED_KEEP = 10


# the job refreshes the matches daily; the cache outlives that, so a page never waits for the batch
KINDRED_CACHE_SECONDS = 3 * 24 * 3600


def _instance_key(year: int) -> str:
    return f"wrapped:instance:{year}"


def compute_instance_stats(year: int) -> dict[str, Any]:
    """The instance's most finished title of the year (public completions), and every local member's
    completion count.

    ``counts[level]`` holds the counts of completions visible at that level, so a page compares the
    member's count for its audience with counts of the same kind. A whole-instance aggregate; it runs
    in a job and the result is cached, never per request.
    """
    tz = ZoneInfo(settings.TIME_ZONE)
    start, end = year_range(year, tz)
    qs = _members().filter(
        parent__shelf_type=ShelfType.COMPLETE,
        created_time__gte=start,
        created_time__lt=end,
        owner__local=True,
        owner__deleted__isnull=True,
        item__is_deleted=False,
    )
    # the top title is shown to anyone, so only public completions count for it
    top = (
        qs.filter(visibility=0)
        .values("item_id")
        .annotate(n=Count("pk"))
        .order_by("-n", "item_id")
        .first()
    )
    per_member = list(
        qs.values("owner_id")
        .annotate(
            n0=Count("pk", filter=Q(visibility=0)),
            n1=Count("pk", filter=Q(visibility__lte=1)),
            n2=Count("pk"),
        )
        .values_list("n0", "n1", "n2")
    )
    counts = [sorted(r[lv] for r in per_member if r[lv]) for lv in range(3)]
    top_item = None
    if top:
        it = Item.objects.filter(pk=top["item_id"]).first()
        if it:
            top_item = {**_card_item(it), "count": top["n"]}
    stats = {"top": top_item, "counts": counts}
    cache.set(_instance_key(year), stats, INSTANCE_CACHE_SECONDS)
    return stats


def _cached_or_queue(key: str, compute: Any, year: int) -> Any:
    """A job's cached result; on a miss, queue the job once and return None so the page stays fast."""
    value = cache.get(key)
    if value is None and cache.add(key + ":lock", 1, 600):
        try:
            django_rq.get_queue("cron").enqueue(compute, year)
        except Exception as e:
            logger.warning(f"unable to queue {key}: {e}")
    return value


def instance_stats(year: int) -> dict[str, Any] | None:
    """Cached instance stats; on a miss, queue the job and return None so the page stays fast."""
    return _cached_or_queue(_instance_key(year), compute_instance_stats, year)


def _percentile(counts: list[int], mine: int) -> int | None:
    if len(counts) < 10 or not mine:
        return None
    below = sum(1 for c in counts if c < mine)
    return round(below * 100 / len(counts))


def _median(counts: list[int]) -> int | None:
    """The titles one real member finished (the lower middle one), with the same minimum as the percentile."""
    if len(counts) < 10:
        return None
    return statistics.median_low(counts)


def _kindred_key(year: int) -> str:
    return f"wrapped:kindred:{year}"


def compute_kindred(year: int) -> dict[int, list[list[float]]]:
    """For each local member, up to KINDRED_KEEP members who could be their kindred member this year,
    best first, as [member id, score, shared titles].

    A whole-instance batch for the job; the result is cached and a page only reads it. Public completions
    only, without bulk-import days (more than BULK_DAY in one day). A candidate is any member, local or
    remote, who is discoverable, not deleted and not on the site's exclude list; whether the member allows
    signed-out visitors does not matter, as that covers only their own pages. Each title weighs
    log((members + 1) / finishers), so a shared rare title counts for more, and titles with more than
    KINDRED_ITEM_CAP finishers are left out; the score is the shared weight divided by the square root of
    the two members' weights, and at least KINDRED_MIN_SHARED titles must be shared. Who the owner
    follows, blocks or mutes changes often, so the page checks that when it is built.
    """
    from catalog.recommendation import excluded_identity_ids  # catalog imports journal

    tz = ZoneInfo(settings.TIME_ZONE)
    start, end = year_range(year, tz)
    qs = _members().filter(
        parent__shelf_type=ShelfType.COMPLETE,
        visibility=0,
        created_time__gte=start,
        created_time__lt=end,
        owner__deleted__isnull=True,
    )
    bulk = set(
        qs.annotate(day=TruncDate("created_time", tzinfo=tz))
        .values("owner_id", "day")
        .annotate(n=Count("pk"))
        .filter(n__gt=BULK_DAY)
        .values_list("owner_id", "day")
    )
    titles: dict[int, set[int]] = defaultdict(set)
    for owner_id, item_id, ts in qs.values_list(
        "owner_id", "item_id", "created_time"
    ).iterator(chunk_size=5000):
        if bulk and (owner_id, ts.astimezone(tz).date()) in bulk:
            continue
        titles[owner_id].add(item_id)
    finishers: dict[int, list[int]] = defaultdict(list)
    for owner_id, items in titles.items():
        for item_id in items:
            finishers[item_id].append(owner_id)
    members = len(titles)
    weight = {
        i: math.log((members + 1) / len(f))
        for i, f in finishers.items()
        if 2 <= len(f) <= KINDRED_ITEM_CAP
    }
    total = {o: sum(weight.get(i, 0.0) for i in items) for o, items in titles.items()}
    excluded = excluded_identity_ids() | set(
        TakaheIdentity.objects.filter(restriction__gt=0).values_list("pk", flat=True)
    )
    local = set(
        APIdentity.objects.filter(pk__in=list(titles), local=True).values_list(
            "pk", flat=True
        )
    )
    result: dict[int, list[list[float]]] = {}
    for a in local:
        mine = [i for i in titles[a] if i in weight]
        if len(mine) < KINDRED_MIN_SHARED:
            continue
        acc: dict[int, list[float]] = {}
        for i in mine:
            w = weight[i]
            for b in finishers[i]:
                if b == a or b in excluded:
                    continue
                e = acc.get(b)
                if e is None:
                    acc[b] = [w, 1]
                else:
                    e[0] += w
                    e[1] += 1
        best = sorted(
            (
                (sw / math.sqrt(total[a] * total[b]), b, int(n))
                for b, (sw, n) in acc.items()
                if n >= KINDRED_MIN_SHARED
            ),
            reverse=True,
        )[:KINDRED_KEEP]
        if best:
            result[a] = [[b, round(score, 4), n] for score, b, n in best]
    cache.set(_kindred_key(year), result, KINDRED_CACHE_SECONDS)
    return result


def kindred_matches(year: int) -> dict[int, list[list[float]]] | None:
    """Cached kindred matches; on a miss, queue the job and return None so the page stays fast."""
    return _cached_or_queue(_kindred_key(year), compute_kindred, year)


# how long a deck update waits for another process to fill a cold instance cache
AGGREGATE_WAIT_SECONDS = 20


def _aggregate(key: str, compute: Any, year: int) -> Any:
    """A cached whole-instance result; on a miss one process computes it (the same lock as the queued job's,
    so a running job counts), and the others wait a little for it. None when the wait runs out."""
    value = cache.get(key)
    if value is not None:
        return value
    if cache.add(key + ":lock", 1, 600):
        return compute(year)
    for _ in range(AGGREGATE_WAIT_SECONDS):
        time.sleep(1)
        value = cache.get(key)
        if value is not None:
            return value
    return None


def year_aggregates(year: int) -> tuple[Any, Any]:
    """The year's instance totals and kindred matches, read once for one deck or a batch of them. A cold cache
    is filled now rather than by a queued job, so a stored deck seldom keeps the gap; when it does (another
    process is still computing), the stored data says ``warming`` and the next page view fills it."""
    return (
        _aggregate(_instance_key(year), compute_instance_stats, year),
        _aggregate(_kindred_key(year), compute_kindred, year),
    )


def aggregates_ready(year: int) -> bool:
    return cache.has_key(_instance_key(year)) and cache.has_key(_kindred_key(year))
