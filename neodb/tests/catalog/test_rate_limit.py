"""Tests for catalog.common.rate_limit.RedisRateLimiter."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from catalog.common.rate_limit import RedisRateLimiter
from catalog.sites.anilist import anilist_limiter
from catalog.sites.igdb import igdb_limiter
from catalog.sites.musicbrainz import musicbrainz_limiter
from catalog.sites.openlibrary import _openlibrary_user_agent, openlibrary_limiter


def _fresh_limiter(rate: float) -> RedisRateLimiter:
    """Limiter pointed at a unique Redis key so tests don't fight each other."""
    return RedisRateLimiter(key=f"test:ratelimit:{uuid.uuid4()}", rate=rate)


def test_first_acquire_is_immediate() -> None:
    """An idle cursor returns a slot in the past; no sleep."""
    rl = _fresh_limiter(rate=10.0)
    t0 = time.monotonic()
    rl.acquire(timeout=2.0)
    assert time.monotonic() - t0 < 0.05


def test_consecutive_acquires_advance_by_interval() -> None:
    """Each acquire reserves the next slot, ~interval seconds later."""
    rl = _fresh_limiter(rate=10.0)  # interval = 0.1s
    rl.acquire(timeout=2.0)  # claims an immediate slot
    t1 = time.monotonic()
    rl.acquire(timeout=2.0)
    gap = time.monotonic() - t1
    # Second acquire waits ~one interval. Allow generous bounds for CI jitter.
    assert 0.05 <= gap < 0.3, f"second acquire waited {gap:.3f}s"


def test_acquire_falls_open_when_queue_exceeds_timeout() -> None:
    """If the reserved slot would land past `timeout`, the cursor refuses to
    advance and the caller proceeds without sleeping."""
    rl = _fresh_limiter(rate=2.0)  # interval = 0.5s
    # Drain so the cursor advances rapidly. With timeout=0.2, the 2nd call's
    # slot (~0.5s ahead) already exceeds the budget.
    rl.acquire(timeout=5.0)
    t0 = time.monotonic()
    rl.acquire(timeout=0.2)
    elapsed = time.monotonic() - t0
    # Should fall through (no sleep) rather than wait the full interval.
    assert elapsed < 0.2, f"fall-open should be instant, took {elapsed:.3f}s"


def test_async_acquire_advances_by_interval() -> None:
    rl = _fresh_limiter(rate=10.0)

    async def run() -> float:
        await rl.acquire_async(timeout=2.0)
        t = time.monotonic()
        await rl.acquire_async(timeout=2.0)
        return time.monotonic() - t

    gap = asyncio.run(run())
    assert 0.05 <= gap < 0.3, f"second async acquire waited {gap:.3f}s"


def test_redis_offline_uses_local_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """If Redis is unreachable, fall back to a local sleep so a single
    process still paces itself at `interval`."""
    rl = _fresh_limiter(rate=10.0)  # interval = 0.1s
    # `None` is the signal _reserve uses to mean "Redis offline."
    monkeypatch.setattr(rl, "_reserve", lambda timeout: None)
    t0 = time.monotonic()
    rl.acquire(timeout=5.0)
    elapsed = time.monotonic() - t0
    # Should sleep ~one interval, not 0 (open) and not the full timeout.
    assert 0.05 <= elapsed < 0.3, f"local fallback slept {elapsed:.3f}s"


def test_try_acquire_takes_a_free_slot() -> None:
    """An idle cursor hands out a slot; the caller may fire."""
    rl = _fresh_limiter(rate=2.0)
    t0 = time.monotonic()
    assert rl.try_acquire() is True
    assert time.monotonic() - t0 < 0.05, "try_acquire must never sleep"


def test_try_acquire_refuses_when_no_slot_is_free() -> None:
    """A second caller inside the same interval is told to skip, not to wait."""
    rl = _fresh_limiter(rate=2.0)  # interval = 0.5s
    assert rl.try_acquire() is True
    t0 = time.monotonic()
    assert rl.try_acquire() is False
    assert time.monotonic() - t0 < 0.05, "refusal must be instant"


def test_try_acquire_refusal_does_not_consume_a_slot() -> None:
    """A skipped caller must not steal the slot the cursor is holding.

    Without this, N concurrent searches would each push the cursor forward and
    starve the scrape paths queued behind them.
    """
    rl = _fresh_limiter(rate=10.0)  # interval = 0.1s
    assert rl.try_acquire() is True
    for _ in range(5):
        assert rl.try_acquire() is False
    # The cursor should still be only one interval out, so a blocking caller
    # waits ~0.1s rather than the ~0.6s six advances would have cost.
    t0 = time.monotonic()
    rl.acquire(timeout=5.0)
    waited = time.monotonic() - t0
    assert waited < 0.3, f"refusals advanced the cursor; waited {waited:.3f}s"


def test_try_acquire_async_matches_sync_semantics() -> None:
    rl = _fresh_limiter(rate=2.0)

    async def run() -> tuple[bool, bool, float]:
        first = await rl.try_acquire_async()
        t0 = time.monotonic()
        second = await rl.try_acquire_async()
        return first, second, time.monotonic() - t0

    first, second, elapsed = asyncio.run(run())
    assert first is True
    assert second is False
    assert elapsed < 0.05, "async refusal must not block the gather fan-out"


def test_try_acquire_burst_admits_concurrent_siblings() -> None:
    """burst=n lets n callers through back to back on an idle host.

    This is what keeps both AniList sites (and both MusicBrainz sites) in the
    results of a single `category=all` query.
    """
    rl = _fresh_limiter(rate=1.0)  # interval = 1s
    assert rl.try_acquire(burst=2) is True
    assert rl.try_acquire(burst=2) is True, "sibling was refused"
    # Credit is spent; the third caller in the same interval still skips.
    assert rl.try_acquire(burst=2) is False


def test_try_acquire_burst_does_not_raise_sustained_rate() -> None:
    """Burst spends accrued credit, it does not widen the interval.

    After the burst the host is exactly one interval further out, not n.
    """
    rl = _fresh_limiter(rate=10.0)  # interval = 0.1s
    assert rl.try_acquire(burst=3) is True
    assert rl.try_acquire(burst=3) is True
    assert rl.try_acquire(burst=3) is True
    t0 = time.monotonic()
    rl.acquire(timeout=5.0)
    waited = time.monotonic() - t0
    # Three slots were handed out, so the cursor sits ~one interval past now,
    # not three intervals: the credit came out of idle time, not the future.
    assert waited < 0.3, f"burst pushed the cursor too far; waited {waited:.3f}s"


def test_burst_credit_does_not_leak_to_strict_callers() -> None:
    """A scrape path (burst=1) sees no tolerance even on a host whose
    searches use burst=2, because the credit is per call, not per cursor."""
    rl = _fresh_limiter(rate=1.0)
    assert rl.try_acquire(burst=1) is True
    assert rl.try_acquire(burst=1) is False


def test_try_acquire_rejects_burst_below_one() -> None:
    rl = _fresh_limiter(rate=1.0)
    with pytest.raises(ValueError):
        rl.try_acquire(burst=0)


def test_try_acquire_allows_when_redis_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken limiter degrades to no limiter, not to a closed door."""
    rl = _fresh_limiter(rate=1.0)
    monkeypatch.setattr(rl, "_reserve", lambda timeout, credit=0.0: None)
    assert rl.try_acquire() is True


def test_musicbrainz_limiter_is_singleton() -> None:
    a = musicbrainz_limiter()
    b = musicbrainz_limiter()
    assert a is b
    assert a.key == "ratelimit:musicbrainz.org"
    # MusicBrainz' documented 1 req/s/IP ceiling.
    assert 1.0 / a.interval <= 1.0


def test_igdb_limiter_is_singleton() -> None:
    a = igdb_limiter()
    b = igdb_limiter()
    assert a is b
    assert a.key == "ratelimit:api.igdb.com"
    # IGDB's documented 4 req/s/client-ID ceiling.
    assert 1.0 / a.interval <= 4.0


def test_anilist_limiter_is_singleton() -> None:
    a = anilist_limiter()
    b = anilist_limiter()
    assert a is b
    assert a.key == "ratelimit:graphql.anilist.co"
    # The live x-ratelimit-limit header has been serving 30/min.
    assert 1.0 / a.interval <= 0.5


def test_openlibrary_limiter_is_singleton() -> None:
    a = openlibrary_limiter()
    b = openlibrary_limiter()
    assert a is b
    assert a.key == "ratelimit:openlibrary.org"
    # OpenLibrary's identified-client tier; the default tier is 1 req/s.
    assert 1.0 / a.interval <= 3.0


def test_openlibrary_user_agent_includes_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The contact address is what earns the 3 req/s tier."""
    from common.models import SiteConfig

    monkeypatch.setattr(SiteConfig.system, "email_from", "ops@example.org")
    ua = _openlibrary_user_agent()
    assert "ops@example.org" in ua
    assert ua.startswith("NeoDB/")


def test_openlibrary_user_agent_falls_back_without_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No contact configured still means an honest UA, never a browser string."""
    from django.conf import settings

    from common.models import SiteConfig

    monkeypatch.setattr(SiteConfig.system, "email_from", "")
    ua = _openlibrary_user_agent()
    assert ua == settings.NEODB_USER_AGENT
    assert "Mozilla" not in ua
