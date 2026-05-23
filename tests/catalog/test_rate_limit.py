"""Tests for catalog.common.rate_limit.RedisRateLimiter."""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from catalog.common.rate_limit import RedisRateLimiter, musicbrainz_limiter


def _fresh_limiter(rate: float, burst: int) -> RedisRateLimiter:
    """A limiter pointed at a unique Redis key so tests don't fight each other."""
    return RedisRateLimiter(
        key=f"test:ratelimit:{uuid.uuid4()}",
        rate=rate,
        burst=burst,
    )


def test_acquire_drains_burst_then_throttles() -> None:
    """Burst tokens come back instantly; the next acquire has to wait."""
    rl = _fresh_limiter(rate=10.0, burst=3)
    start = time.monotonic()
    for _ in range(3):
        rl.acquire(timeout=2.0)
    burst_elapsed = time.monotonic() - start

    # Three immediate acquires must complete well under one refill period.
    assert burst_elapsed < 0.2, f"burst should be instant, took {burst_elapsed:.3f}s"

    # The 4th acquire has to wait for a refill (~0.1s at 10/s).
    t0 = time.monotonic()
    rl.acquire(timeout=2.0)
    waited = time.monotonic() - t0
    assert 0.05 <= waited < 0.4, f"4th acquire wait {waited:.3f}s out of range"


def test_acquire_timeout_falls_open() -> None:
    """Timeout returns without raising so the caller can still attempt."""
    rl = _fresh_limiter(rate=0.5, burst=1)
    rl.acquire(timeout=1.0)  # drains the only token
    t0 = time.monotonic()
    rl.acquire(timeout=0.2)  # 2s refill, can't complete in 0.2s
    elapsed = time.monotonic() - t0
    # Should have waited about the timeout, then returned.
    assert 0.1 <= elapsed < 0.6, f"timeout fallthrough took {elapsed:.3f}s"


def test_acquire_async_drains_burst_then_throttles() -> None:
    rl = _fresh_limiter(rate=10.0, burst=3)

    async def run() -> tuple[float, float]:
        t0 = time.monotonic()
        for _ in range(3):
            await rl.acquire_async(timeout=2.0)
        burst_elapsed = time.monotonic() - t0
        t1 = time.monotonic()
        await rl.acquire_async(timeout=2.0)
        wait_elapsed = time.monotonic() - t1
        return burst_elapsed, wait_elapsed

    burst_elapsed, wait_elapsed = asyncio.run(run())
    assert burst_elapsed < 0.2
    assert 0.05 <= wait_elapsed < 0.4


def test_redis_offline_falls_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """If Redis is unreachable, acquire returns immediately instead of blocking."""
    rl = _fresh_limiter(rate=10.0, burst=1)
    # `None` is the signal _try_take uses to mean "Redis offline."
    monkeypatch.setattr(rl, "_try_take", lambda: None)
    t0 = time.monotonic()
    rl.acquire(timeout=5.0)
    assert time.monotonic() - t0 < 0.05


def test_musicbrainz_limiter_is_singleton() -> None:
    a = musicbrainz_limiter()
    b = musicbrainz_limiter()
    assert a is b
    assert a.key == "ratelimit:musicbrainz.org"
    # Stays comfortably under MB's published 50 req/s/IP cap.
    assert a.rate <= 40.0
