"""Redis-backed slot-reservation rate limiter for cross-process throttling.

A single Redis key stores the earliest wall-clock time at which the next
request to a host may fire. Every caller atomically advances that cursor by
``interval`` seconds and sleeps until its assigned slot, so every NeoDB
process (web, RQ worker, management command) competes for the same slots on
the shared Redis.

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
# Returns the wall-clock time at which the caller may proceed, or "-1" to
# signal "queue is full, fall open".
_RESERVE_SLOT_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local max_wait = tonumber(ARGV[3])
local current = tonumber(redis.call('GET', key)) or 0
local target = current
if target < now then target = now end
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

    def _reserve(self, timeout: float) -> float | None:
        """Atomically claim the next slot.

        Returns the wall-clock time the caller should fire at, ``None`` when
        Redis is unreachable, or a value <= now-1 when the cursor declined to
        advance because the wait would exceed ``timeout``.
        """
        script = self._load_script()
        if script is None:
            return None
        try:
            result = script(
                keys=[self.key],
                args=[time.time(), self.interval, timeout],
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
