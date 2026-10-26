# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The per-organisation team-membership epoch, bumped by the team service itself (#19630).

A cached team membership (``knowledge/tenant_groups.py``) must not outlive a revocation in ANY
worker. The counter lives in the knowledge Redis database beside the ACL epoch
(``knowledge/acl_epoch.py``, which re-exports these names: one mechanism, a scoped key), and it
is bumped here, in ``autobot_shared``, so every ``TeamService`` writer -- the backend and the
SLM alike -- invalidates without being handed a hook it could omit. One counter per organisation,
so a membership change does not flush the RAG result caches that key on the global ACL epoch.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from autobot_shared.env_utils import env_float_clamped, env_int_clamped
from autobot_shared.logging_manager import get_logger
from autobot_shared.redis_client import get_async_redis_client

logger = get_logger(__name__)

MEMBERSHIP_EPOCH_KEY_PREFIX = "kb:membership_epoch:"
MEMBERSHIP_EPOCH_DATABASE = "knowledge"

#: Tries a failed post-commit bump gets before it is reported (env-backed).
MEMBERSHIP_EPOCH_BUMP_ATTEMPTS = env_int_clamped("AUTOBOT_MEMBERSHIP_EPOCH_BUMP_ATTEMPTS", 3, 1, 10)
#: Seconds before the second try; doubles on each further try (env-backed).
MEMBERSHIP_EPOCH_BUMP_BACKOFF_SECONDS = env_float_clamped(
    "AUTOBOT_MEMBERSHIP_EPOCH_BUMP_BACKOFF_SECONDS", 0.2, 0.0, 10.0
)


class MembershipEpochBumpError(RuntimeError):
    """The epoch could not be bumped after a revocation committed: caches may serve a removed member."""


def membership_epoch_key(org_id: uuid.UUID | str | None) -> str:
    """The counter key of *org_id*; teams with no organisation share the empty-organisation key."""
    return f"{MEMBERSHIP_EPOCH_KEY_PREFIX}{org_id or ''}"


async def read_membership_epoch(org_id: uuid.UUID | str | None) -> str | None:
    """The organisation's epoch as text, ``"0"`` before the first bump, ``None`` when unreadable.

    ``None`` means "serve and fill no cache": an unreadable epoch cannot prove an entry predates
    no revocation.
    """
    try:
        client = await get_async_redis_client(database=MEMBERSHIP_EPOCH_DATABASE)
        if client is None:
            return None
        raw = await client.get(membership_epoch_key(org_id))
    except Exception as exc:
        logger.warning("KB membership epoch unreadable; team caches are skipped: %s", exc)
        return None
    if raw is None:
        return "0"
    return raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)


async def _bump_once(org_id: uuid.UUID | str | None) -> int:
    client = await get_async_redis_client(database=MEMBERSHIP_EPOCH_DATABASE)
    if client is None:
        raise MembershipEpochBumpError("no Redis client")
    return int(await client.incr(membership_epoch_key(org_id)))


async def bump_membership_epoch(org_id: uuid.UUID | str | None) -> int:
    """INCR the organisation's epoch, retrying a bounded number of times with backoff.

    Every failed try is logged at error level; exhausting the tries raises
    ``MembershipEpochBumpError`` -- the revocation is durable, so the caller must learn that its
    invalidation did not happen. There is no outbox: staleness is then bounded by the cache TTL.
    """
    delay = MEMBERSHIP_EPOCH_BUMP_BACKOFF_SECONDS
    for attempt in range(1, MEMBERSHIP_EPOCH_BUMP_ATTEMPTS + 1):
        try:
            return await _bump_once(org_id)
        except Exception as exc:
            logger.error(
                "KB membership epoch bump failed for org %s (try %d/%d): %s",
                org_id, attempt, MEMBERSHIP_EPOCH_BUMP_ATTEMPTS, exc,
            )  # fmt: skip
            if attempt == MEMBERSHIP_EPOCH_BUMP_ATTEMPTS:
                raise MembershipEpochBumpError(f"membership epoch not bumped for org {org_id}: {exc}") from exc
            await asyncio.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")  # pragma: no cover


@dataclass(frozen=True)
class MembershipEpochBump:
    """A queueable post-commit bump; equal for one organisation, so a transaction queues it once."""

    org_id: uuid.UUID | str | None

    async def __call__(self) -> int:
        return await bump_membership_epoch(self.org_id)
