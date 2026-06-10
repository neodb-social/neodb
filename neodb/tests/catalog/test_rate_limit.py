"""Tests for catalog.common.rate_limit.RedisRateLimiter."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from catalog.common.rate_limit import RedisRateLimiter
from catalog.sites.musicbrainz import musicbrainz_limiter


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


def test_musicbrainz_limiter_is_singleton() -> None:
    a = musicbrainz_limiter()
    b = musicbrainz_limiter()
    assert a is b
    assert a.key == "ratelimit:musicbrainz.org"
    # MusicBrainz' documented 1 req/s/IP ceiling.
    assert 1.0 / a.interval <= 1.0
