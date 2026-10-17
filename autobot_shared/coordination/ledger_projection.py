# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The install-wide ledger projection: live agent state the SLM can read (#18632).

Owner ruling (2026-10-17, on #18632): presence -- which includes AI-stack agents
(kind ``ai_stack``) and live sessions (kind ``session``) --, pending peer-message
METADATA and idle subscriptions live only inside the user backend's process
(`protocols/agent_presence.py`, `peer_inbox.py`, `idle_notice.py`). The process
stays their system of record (`store_authority` concept ``agent_live_state``);
the user backend publishes a snapshot of each to Redis every presence sweep, and
the SLM admin ledger reads that snapshot. Nothing writes the projection but the
publisher, and nothing treats it as truth: it is a timestamped copy that expires.

A snapshot that is missing -- never published, or not refreshed within
``AUTOBOT_LEDGER_PROJECTION_TTL_S`` because the user backend is down -- is
:class:`LedgerUnreadable`, the typed 503, never an empty list. Peer messages are
counts and sender names only; a message's content never leaves the process.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from autobot_shared.coordination.ledger_read import GENERIC_DETAIL, LedgerUnreadable, _read
from autobot_shared.coordination.ledger_schemas import IdleSubscriberRow, PeerMessageRow, PresenceRow
from autobot_shared.env_utils import env_int_clamped
from autobot_shared.redis_client import get_async_redis_client
from autobot_shared.store_authority import system_of_record
from autobot_shared.time_utils import utc_timestamp

SYSTEM_OF_RECORD = system_of_record("agent_live_state")

#: How long a snapshot stays readable without a refresh. Longer than the presence
#: sweep (AUTOBOT_AGENT_PRESENCE_SYNC_INTERVAL_SECONDS, default 30s) so one slow
#: sweep does not blank the SLM; short enough that a stopped backend reads as
#: "couldn't read", not as a stale picture of agents that are gone.
PROJECTION_TTL_S = env_int_clamped("AUTOBOT_LEDGER_PROJECTION_TTL_S", 120, min_v=10, max_v=3600)

SOURCE_PRESENCE = "presence"
SOURCE_PEER_MESSAGES = "peer_messages"
SOURCE_IDLE_SUBSCRIBERS = "idle_subscribers"
SOURCES = (SOURCE_PRESENCE, SOURCE_PEER_MESSAGES, SOURCE_IDLE_SUBSCRIBERS)

NOT_PUBLISHED_DETAIL = "no snapshot: the user backend has not published this within the projection TTL"

Row = TypeVar("Row", bound=BaseModel)


def projection_key(source: str) -> str:
    return f"autobot:ledger:projection:{source}"


def _snapshot(rows: Sequence[BaseModel]) -> str:
    return json.dumps({"published_at": utc_timestamp(), "rows": [row.model_dump(mode="json") for row in rows]})


async def publish(
    client: Any,
    *,
    presence: list[PresenceRow],
    peer_messages: list[PeerMessageRow],
    idle_subscribers: list[IdleSubscriberRow],
) -> None:
    """Replace all three snapshots in one transaction, each expiring after the TTL."""
    pipe = client.pipeline(transaction=True)
    for source, rows in (
        (SOURCE_PRESENCE, presence),
        (SOURCE_PEER_MESSAGES, peer_messages),
        (SOURCE_IDLE_SUBSCRIBERS, idle_subscribers),
    ):
        pipe.set(projection_key(source), _snapshot(rows), ex=PROJECTION_TTL_S)
    await pipe.execute()


async def _load(source: str) -> dict:
    client = await get_async_redis_client(database="main")
    if client is None:
        raise LedgerUnreadable(source, GENERIC_DETAIL)
    raw = await client.get(projection_key(source))
    if raw is None:
        raise LedgerUnreadable(source, NOT_PUBLISHED_DETAIL)
    return json.loads(raw)


async def read_snapshot(source: str, model: type[Row]) -> tuple[list[Row], str]:
    """``(rows, published_at)`` for *source*; a missing snapshot is :class:`LedgerUnreadable`."""
    snapshot = await _read(source, lambda: _load(source))
    return [model.model_validate(row) for row in snapshot["rows"]], snapshot["published_at"]


async def published_at(source: str) -> str | None:
    """When *source* was last published, or None when there is no live snapshot. Never raises for absence."""
    try:
        return (await _load(source))["published_at"]
    except LedgerUnreadable:
        return None
