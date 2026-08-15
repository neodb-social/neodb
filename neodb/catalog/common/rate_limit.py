"""Redis-backed slot-reservation rate limiter for cross-process throttling.

A single Redis key stores the earliest wall-clock time at which the next
request to a host may fire. Every caller atomically advances that cursor by
``interval`` seconds and sleeps until its assigned slot, so every NeoDB
process (web, RQ worker, management command) competes for the same slots on
the shared Redis.

Two acquisition modes:

* :meth:`RedisRateLimiter.acquire` blocks until its slot. Use it on scrape and
  batch paths, where waiting is cheaper than being throttled upstream.
* :meth:`RedisRateLimiter.try_acquire` never blocks: it takes a slot if one is
  free right now and otherwise returns ``False`` so the caller can skip the
  request entirely. Use it on the interactive external-search fan-out, where
  a wait would stall the whole result page. Its ``burst`` argument spends
  credit the host accrued while idle, for the cases where several callers
  legitimately fire together; the sustained rate is unaffected.

Failure modes are advisory rather than fatal:

* If Redis is unreachable the limiter falls through without sleeping; the
  caller still makes the request and the upstream service is the source of
  truth for hard rate limits.
* If the reserved slot is further than ``timeout`` seconds in the future the
  Lua script declines to advance the cursor (so a thundering herd can't push
  ``next_allowed_at`` into the distant future) and the caller falls through.
* In ``use_local_response`` test mode the limiter is a no-op.
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

# Reserve the next request slot atomically.
# KEYS[1] = cursor key. ARGV[1] = now (float seconds). ARGV[2] = interval
# (seconds between consecutive requests). ARGV[3] = max_wait (seconds; refuse
# to advance the cursor if a caller would end up waiting longer than this).
# ARGV[4] = credit (seconds the cursor may lag behind `now`, letting an idle
# host absorb a short burst before the interval starts biting).
# Returns the wall-clock time at which the caller may proceed, or "-1" to
# signal "queue is full, fall open".
_RESERVE_SLOT_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local max_wait = tonumber(ARGV[3])
local credit = tonumber(ARGV[4])
local current = tonumber(redis.call('GET', key)) or 0
local floor = now - credit
local target = current
if target < floor then target = floor end
if target - now > max_wait then
  return '-1'
end
local next_slot = target + interval
-- Expire well past the largest legitimate wait so an idle key reclaims itself.
local ttl_ms = math.ceil((max_wait + interval) * 1000) + 5000
redis.call('SET', key, tostring(next_slot), 'PX', ttl_ms)
return tostring(target)
"""


class RedisRateLimiter:
    """Reserve the next request slot via a shared Redis cursor.

    Construct once per (key, rate) tuple, typically as a module-level singleton
    per remote host. Thread-safe; the Lua reservation is atomic.
    """

    def __init__(self, key: str, rate: float, queue: str = "fetch"):
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.key = key
        self.interval = 1.0 / float(rate)
        self.queue = queue
        self._script_lock = threading.Lock()
        self._script: "Script | None" = None

    def _load_script(self) -> "Script | None":
        with self._script_lock:
            if self._script is not None:
                return self._script
            try:
                conn = django_rq.get_connection(self.queue)
                self._script = conn.register_script(_RESERVE_SLOT_LUA)
            except Exception as e:  # pragma: no cover -- defensive
                logger.warning(f"rate-limit script load failed for {self.key}: {e}")
                return None
            return self._script

    def _credit(self, burst: int) -> float:
        """Seconds of lag the cursor may carry for a ``burst``-sized caller.

        ``burst=1`` is strict serialization (no credit). ``burst=n`` lets an
        idle host serve n requests back to back, after which the interval
        applies as usual, so the sustained rate is unchanged.
        """
        if burst < 1:
            raise ValueError("burst must be >= 1")
        return (burst - 1) * self.interval

    def _reserve(self, timeout: float, credit: float = 0.0) -> float | None:
        """Atomically claim the next slot.

        Returns the wall-clock time the caller should fire at, ``None`` when
        Redis is unreachable, or a value <= now-1 when the cursor declined to
        advance because the wait would exceed ``timeout``. The returned time
        may sit slightly in the past when ``credit`` is in play; callers treat
        anything not in the future as "fire now".
        """
        script = self._load_script()
        if script is None:
            return None
        try:
            result = script(
                keys=[self.key],
                args=[time.time(), self.interval, timeout, credit],
            )
        except Exception as e:
            logger.warning(f"rate-limit redis error for {self.key}: {e}")
            return None
        if isinstance(result, bytes):
            result = result.decode()
        return float(result)

    def _local_fallback_sleep(self) -> None:
        """Sleep one full interval so a single process still self-paces when
        Redis is unreachable. No cross-process coordination here -- N workers
        will independently each fire 1/interval req/s, so the aggregate rate
        degrades to N×rate. That's worse than the cross-process throttle but
        a lot better than letting everyone burst freely."""
        time.sleep(self.interval)

    def acquire(self, timeout: float = 15.0) -> None:
        """Block until the reserved slot, capped at ``timeout`` seconds."""
        if get_mock_mode():
            return
        target = self._reserve(timeout)
        if target is None:
            # Redis offline -- fall back to a local sleep so we still pace
            # within this process, even though we lose cross-process coord.
            self._local_fallback_sleep()
            return
        wait = target - time.time()
        if wait <= 0:
            # Either we got a slot in the past (we're idle) or the cursor
            # declined to advance because the queue was too long.
            if target < 0:
                logger.warning(
                    f"rate-limit slot for {self.key} would exceed "
                    f"{timeout}s; proceeding without throttle"
                )
            return
        time.sleep(wait)

    def _slot_is_free(self, target: float | None) -> bool:
        """Interpret a ``_reserve(0.0)`` result as "may I fire right now?".

        With ``max_wait=0`` the Lua script only advances the cursor when the
        slot it would hand out is not in the future, so a ``False`` here means
        we consumed nothing and the next caller still sees a full slot.

        Redis being unreachable resolves to ``True``: the module's contract is
        that a broken limiter degrades to no limiter, not to a closed door.
        """
        if target is None:
            return True
        return target - time.time() <= 0 and target >= 0

    def try_acquire(self, burst: int = 1) -> bool:
        """Take a slot if one is free right now; never block.

        Returns ``True`` when the caller may fire immediately and ``False``
        when it should skip the request. Unlike :meth:`acquire` a refusal is
        silent -- skipping is the designed outcome here, not an anomaly worth
        a warning on every interactive search.

        Pass ``burst=n`` when n callers legitimately fire together against one
        host, as the sibling search sites do (AniList anime plus manga,
        MusicBrainz release plus artist). Without it the second sibling is
        refused every time and its half of the results disappears from every
        ``category=all`` query. Burst spends credit the host accrued while
        idle, so the sustained rate stays at ``rate``.
        """
        if get_mock_mode():
            return True
        return self._slot_is_free(self._reserve(0.0, self._credit(burst)))

    async def try_acquire_async(self, burst: int = 1) -> bool:
        """Async variant of :meth:`try_acquire`.

        The reservation goes to a worker thread for the same reason
        :meth:`acquire_async` does it: redis-py blocks, and this runs inside
        an ``asyncio.gather`` fan-out where one blocking call stalls every
        other site's search.
        """
        if get_mock_mode():
            return True
        credit = self._credit(burst)
        return self._slot_is_free(await asyncio.to_thread(self._reserve, 0.0, credit))

    async def acquire_async(self, timeout: float = 15.0) -> None:
        """Async variant of :meth:`acquire`.

        Redis-py's client is blocking, so run the script call on a worker
        thread; otherwise an `asyncio.gather` fan-out (e.g. the external
        search dispatcher) would stall every concurrent coroutine on each
        reservation, even sub-millisecond ones.
        """
        if get_mock_mode():
            return
        target = await asyncio.to_thread(self._reserve, timeout)
        if target is None:
            await asyncio.sleep(self.interval)
            return
        wait = target - time.time()
        if wait <= 0:
            if target < 0:
                logger.warning(
                    f"rate-limit slot for {self.key} would exceed "
                    f"{timeout}s; proceeding without throttle"
                )
            return
        await asyncio.sleep(wait)
