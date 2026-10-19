# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Failed issue-claims credential checks: rate-limited per source, audited in aggregate (#18811).

A presented ``<uuid>.<verifier>`` costs a store lookup, and an unauthenticated caller can present
as many as it likes. So:

* **Limit.** Failed checks are counted per source -- the client IP from the canonical
  ``autobot_shared.proxy_utils.get_client_ip`` -- in the shared ``RateLimiter``'s custom window.
  Once a source has ``AUTOBOT_ISSUE_CLAIMS_FAILURE_LIMIT`` failures inside
  ``AUTOBOT_ISSUE_CLAIMS_FAILURE_WINDOW_S``, its next presentation is refused with 429 before any
  store lookup. A success is not counted, and one source's failures never limit another's.
* **Aggregate.** A failure is not one audit row each. The first failure of a source opens a
  window; every failure and every 429 in it is tallied (in Redis, so all workers share one tally;
  in-process while Redis is unreachable), and when the window closes ONE SECURITY event,
  ``claims_credential.check_failures``, records the counts. Successful checks are still audited
  one by one, by ``SecretsCoordinator.verify_bearer``.
"""

from __future__ import annotations

import asyncio
from collections import Counter

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from autobot_shared.env_utils import env_int_clamped
from autobot_shared.logging_manager import get_logger
from autobot_shared.proxy_utils import get_client_ip
from autobot_shared.rate_limiter import RateLimiter
from autobot_shared.redis_client import get_async_redis_client
from services.audit.audit import emit_security
from services.audit.taxonomy import AuditAction

logger = get_logger(__name__)

#: The window failed checks are counted and aggregated over.
FAILURE_WINDOW_S = env_int_clamped("AUTOBOT_ISSUE_CLAIMS_FAILURE_WINDOW_S", 300, min_v=10, max_v=86400)
#: Failed checks one source may make in a window before it is refused without a lookup.
FAILURE_LIMIT = env_int_clamped("AUTOBOT_ISSUE_CLAIMS_FAILURE_LIMIT", 10, min_v=1, max_v=10000)
_TALLY_PREFIX = "autobot:issue-claims:credential-failures"
FAILED, THROTTLED = "failures", "throttled"
UNKNOWN_SOURCE = "unknown"


def client_source(request: Request) -> str:
    """The per-source key: the client IP, never a raw forwarded header."""
    return get_client_ip(request) or UNKNOWN_SOURCE


class FailureTally:
    """Per-source counts of failed and throttled checks, flushed as one audit event per window."""

    def __init__(self, accessor: str) -> None:
        self._accessor = accessor
        self._local: dict[str, Counter] = {}
        self._pending: set[asyncio.Task] = set()

    async def add(self, source: str, field: str) -> None:
        """Count one *field* for *source*; the add that opens a window schedules its flush."""
        opened = await self._add_shared(source, field)
        if opened is None:
            opened = self._add_local(source, field)
        if opened:
            asyncio.get_running_loop().call_later(FAILURE_WINDOW_S, self._start_flush, source)

    async def _add_shared(self, source: str, field: str) -> bool | None:
        """Tally in Redis; whether this add opened the window, ``None`` when Redis is unreachable."""
        redis = await get_async_redis_client()
        if redis is None:
            return None
        key = f"{_TALLY_PREFIX}:{source}"
        try:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.hsetnx(key, "opened", 1)
                pipe.hincrby(key, field, 1)
                opened, _ = await pipe.execute()
            if opened:
                # An owner that dies before its flush leaves a key that still ends, so a new window opens.
                await redis.expire(key, 2 * FAILURE_WINDOW_S)
        except (RedisError, OSError) as exc:
            logger.warning("issue-claims failure tally fell back to in-process (%s)", type(exc).__name__)
            return None
        return bool(opened)

    def _add_local(self, source: str, field: str) -> bool:
        opened = source not in self._local
        self._local.setdefault(source, Counter())[field] += 1
        return opened

    def _start_flush(self, source: str) -> None:
        task = asyncio.get_running_loop().create_task(self.flush(source))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _take_shared(self, source: str) -> Counter:
        redis = await get_async_redis_client()
        if redis is None:
            return Counter()
        key = f"{_TALLY_PREFIX}:{source}"
        try:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.hgetall(key)
                pipe.delete(key)
                fields, _ = await pipe.execute()
        except (RedisError, OSError) as exc:
            logger.error("issue-claims failure tally unreadable at flush (%s)", type(exc).__name__)
            return Counter()
        named = {(f.decode("utf-8") if isinstance(f, bytes) else f): int(v) for f, v in fields.items()}
        return Counter({f: n for f, n in named.items() if f in (FAILED, THROTTLED)})

    async def flush(self, source: str) -> Counter:
        """Close *source*'s window: emit its one aggregated audit event; the counts it carried."""
        counts = await self._take_shared(source) + self._local.pop(source, Counter())
        if counts:
            emit_security(
                AuditAction.CLAIMS_CREDENTIAL_CHECK_FAILURES,
                UNKNOWN_SOURCE,
                resource_type="secret",
                metadata={
                    "accessor": self._accessor,
                    "source": source,
                    "window_s": FAILURE_WINDOW_S,
                    FAILED: counts[FAILED],
                    THROTTLED: counts[THROTTLED],
                },
                ip_address=None if source == UNKNOWN_SOURCE else source,
                outcome="failure",
            )
        return counts


class FailureThrottle:
    """The per-source failed-check limit plus its aggregated audit."""

    def __init__(self, accessor: str) -> None:
        self.tally = FailureTally(accessor)
        self._limiter = RateLimiter("issue-claims-credential-failure", default_tier="anonymous")

    async def refuse_if_throttled(self, source: str) -> None:
        """429, before any store lookup, once *source* has used up its failures for the window."""
        if await self._limiter.get_remaining_in_window(source, FAILURE_LIMIT, FAILURE_WINDOW_S) > 0:
            return
        await self.tally.add(source, THROTTLED)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed issue-claims credential checks; try again later",
            headers={"Retry-After": str(FAILURE_WINDOW_S)},
        )

    async def record_failure(self, source: str) -> None:
        """Count one failed check against *source*, in the limit and in the window's tally."""
        await self._limiter.acquire_window(source, FAILURE_LIMIT, FAILURE_WINDOW_S)
        await self.tally.add(source, FAILED)
