"""Redis-backed token-bucket rate limiter for cross-process throttling.

Used by site scrapers that share a per-host quota across web/worker processes
on the same Redis. The bucket lives entirely in Redis so every Django process,
RQ worker, and management command compete for the same tokens.

The limiter is advisory: if Redis is unreachable or the timeout elapses we
return without raising so the caller can still attempt the request and rely
on the upstream service for hard enforcement.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING

import django_rq
from loguru import logger

from .downloaders import get_mock_mode

if TYPE_CHECKING:
    from redis.client import Script

# Atomic token-bucket implementation. KEYS[1] = bucket key. ARGV[1] = refill
# rate (tokens/sec). ARGV[2] = capacity. ARGV[3] = caller's wall-clock time
# (seconds, float). ARGV[4] = cost (usually 1). Returns {allowed, wait_secs}
# where allowed is 1 or 0 and wait_secs (string, for protocol compat) is the
# time until enough tokens accumulate for the request that was just denied.
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])

if tokens == nil then
  tokens = capacity
  ts = now
end

local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * rate)

local allowed = 0
local wait = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  wait = (cost - tokens) / rate
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
-- Expire well after a full refill window so idle buckets reclaim themselves.
redis.call('PEXPIRE', key, math.ceil((capacity / rate) * 1000) + 5000)

return {allowed, tostring(wait)}
"""


class RedisRateLimiter:
    """Token-bucket limiter shared across processes via a single Redis key.

    Construct once per (key, rate, burst) tuple — typically as a module-level
    singleton per remote host. Thread-safe; the underlying Redis script call
    is atomic.
    """

    def __init__(
        self,
        key: str,
        rate: float,
        burst: int | None = None,
        queue: str = "fetch",
    ):
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.key = key
        self.rate = float(rate)
        # Default burst to one second of headroom so callers can drain the
        # bucket quickly then settle into the sustained rate.
        self.burst = int(burst) if burst is not None else max(1, int(rate))
        self.queue = queue
        self._script_lock = threading.Lock()
        self._script: "Script | None" = None

    def _load_script(self) -> "Script | None":
        with self._script_lock:
            if self._script is not None:
                return self._script
            try:
                conn = django_rq.get_connection(self.queue)
                self._script = conn.register_script(_TOKEN_BUCKET_LUA)
            except Exception as e:  # pragma: no cover -- defensive
                logger.warning(f"rate-limit script load failed for {self.key}: {e}")
                return None
            return self._script

    def _try_take(self) -> tuple[bool, float] | None:
        """Atomically attempt to take one token.

        Returns ``(allowed, wait_seconds)`` on success, or ``None`` when Redis
        is unreachable -- caller should fall through to "no limit" behavior.
        """
        script = self._load_script()
        if script is None:
            return None
        try:
            result = script(
                keys=[self.key],
                args=[self.rate, self.burst, time.time(), 1],
            )
        except Exception as e:
            logger.warning(f"rate-limit redis error for {self.key}: {e}")
            return None
        allowed = bool(int(result[0]))
        # redis-py returns bytes for the string element; decode defensively.
        raw_wait = result[1]
        if isinstance(raw_wait, bytes):
            raw_wait = raw_wait.decode()
        return allowed, float(raw_wait)

    def acquire(self, timeout: float = 30.0) -> None:
        """Block (sleep) until a token is taken or ``timeout`` elapses.

        On timeout this returns without raising -- the limiter is advisory and
        the caller will still attempt the request. Bypassed under mock mode
        so test runs don't pay for the throttle.
        """
        if get_mock_mode():
            return
        deadline = time.monotonic() + timeout
        while True:
            taken = self._try_take()
            if taken is None:
                # Redis offline: fail open.
                return
            allowed, wait = taken
            if allowed:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    f"rate-limit acquire timed out for {self.key}; "
                    f"proceeding without a token"
                )
                return
            # Cap individual sleeps so a stale `wait` from a noisy clock
            # doesn't park us for minutes.
            time.sleep(min(wait, remaining, 0.5))

    async def acquire_async(self, timeout: float = 30.0) -> None:
        """Async variant of :meth:`acquire`; yields control while waiting."""
        if get_mock_mode():
            return
        deadline = time.monotonic() + timeout
        while True:
            # The Redis call itself is sub-millisecond; running it inline
            # avoids the overhead of `asyncio.to_thread` for the common path
            # (token available immediately).
            taken = self._try_take()
            if taken is None:
                return
            allowed, wait = taken
            if allowed:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    f"rate-limit async acquire timed out for {self.key}; "
                    f"proceeding without a token"
                )
                return
            await asyncio.sleep(min(wait, remaining, 0.5))


# MusicBrainz publishes a 50 req/s/IP cap. Run at 40 to leave headroom for
# clock skew, bursts at the very edge of the window, and unrelated tooling
# on the same egress IP.
_MB_RATE = 40.0
_MB_BURST = 40

_musicbrainz_limiter: RedisRateLimiter | None = None


def musicbrainz_limiter() -> RedisRateLimiter:
    """Singleton limiter for musicbrainz.org calls."""
    global _musicbrainz_limiter
    if _musicbrainz_limiter is None:
        _musicbrainz_limiter = RedisRateLimiter(
            key="ratelimit:musicbrainz.org",
            rate=_MB_RATE,
            burst=_MB_BURST,
        )
    return _musicbrainz_limiter
