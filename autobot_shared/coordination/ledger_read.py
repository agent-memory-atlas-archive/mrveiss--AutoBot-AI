# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Read-only views over the coordination stores, shared by both ledger APIs (#18391).

The user backend (scoped to one company) and the SLM backend (install-wide,
admin only) read the same Redis, so the reading lives here once and the two
routers only decide *whose* rows they may show. Nothing in this module writes,
releases, deletes or reorders anything -- the stores' own modules stay the only
way to change a claim.

**Unreadable is not empty.** Every function here raises :class:`LedgerUnreadable`
when its store cannot be read, naming the source. A router turns that into
HTTP 503; an empty result therefore always means the store was read and held
nothing (`MEASUREMENT_DISCIPLINE.md`: *nothing found* versus *did not look*).

**Company scoping is by agent identity.** A claim carries ``agent_id``, not a
company. A caller passes ``agent_ids`` -- the agent slugs its company owns -- and
every row whose agent is not in that set is dropped, as is a row naming no
agent. Fail closed: an agent nobody can attribute to a company is install-wide
and is shown only to the install-wide view (``agent_ids=None``).
"""

from __future__ import annotations

import functools
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, TypeVar

from redis.exceptions import RedisError

from autobot_shared.coordination import dev_loop_actions
from autobot_shared.coordination.claim_waitlist import Waiter, all_waiters
from autobot_shared.coordination.ledger_events import (
    CLAIM_EXPIRED,
    EVENT_CAP,
    RELEASE_REFUSED,
    YIELD_TIMEOUT_S,
    read_events,
    unanswered_yields,
)
from autobot_shared.coordination.ledger_schemas import (
    ActivityResponse,
    ActivityRow,
    ClaimRow,
    ClaimsResponse,
    ConflictRow,
    ConflictsResponse,
    ContentionResponse,
    ContentionRow,
    DevLoopActionRow,
    DevLoopHistoryResponse,
    DevLoopIssuesResponse,
    WaiterRow,
    WaitlistsResponse,
)
from autobot_shared.coordination.work_claims import ClaimUnavailable, WorkClaim, claim_payload, list_claims
from autobot_shared.redis_client import get_async_redis_client

T = TypeVar("T")

#: Shared, contended resources (#16951): the scope kinds the SLM contention view shows.
RESOURCE_KINDS = ("provider", "cpu", "queue")

SOURCE_CLAIMS = "claims"
SOURCE_WAITLISTS = "waitlists"
SOURCE_EVENTS = "events"
SOURCE_DEV_LOOP = "dev_loop"

AgentIds = frozenset[str] | None


class LedgerUnreadable(Exception):
    """A coordination store could not be read -- distinct from the store being empty."""

    def __init__(self, source: str, detail: str) -> None:
        super().__init__(f"{source}: {detail}")
        self.source = source
        self.detail = detail


async def _read(source: str, call: Callable[[], Awaitable[T]]) -> T:
    """Run *call*, turning every 'cannot reach Redis' shape into :class:`LedgerUnreadable`."""
    try:
        return await call()
    except (ClaimUnavailable, RuntimeError, RedisError, OSError) as exc:
        raise LedgerUnreadable(source, str(exc) or type(exc).__name__) from exc


def _visible(agent_ids: AgentIds, *agents: str | None) -> bool:
    """True when the caller may see a row involving *agents*. ``None`` scope sees all."""
    return agent_ids is None or any(a is not None and a in agent_ids for a in agents)


def _claim_row(claim: WorkClaim) -> ClaimRow:
    return ClaimRow(**claim_payload(claim))


def _waiter_rows(scope: str, queue: list[Waiter]) -> list[WaiterRow]:
    return [
        WaiterRow(
            scope=scope,
            position=i,
            agent_id=w.agent_id,
            task_id=w.task_id,
            mode=w.mode,
            intent=w.intent,
            priority=w.priority,
            joined_at=w.joined_at,
            expires_at=w.expires_at,
        )
        for i, w in enumerate(queue, start=1)
    ]


async def _claims(agent_ids: AgentIds, kind: str | None) -> list[ClaimRow]:
    held = await _read(SOURCE_CLAIMS, lambda: list_claims(kind))
    rows = [_claim_row(c) for c in held if _visible(agent_ids, c.agent_id)]
    return sorted(rows, key=lambda r: (r.expires_at, r.scope))


async def _waiters(agent_ids: AgentIds) -> list[WaiterRow]:
    queues = await _read(SOURCE_WAITLISTS, all_waiters)
    rows = [row for scope, queue in sorted(queues.items()) for row in _waiter_rows(scope, queue)]
    return [r for r in rows if _visible(agent_ids, r.agent_id)]


async def claims(agent_ids: AgentIds = None, kind: str | None = None) -> ClaimsResponse:
    rows = await _claims(agent_ids, kind)
    return ClaimsResponse(claims=rows, count=len(rows))


async def waitlists(agent_ids: AgentIds = None) -> WaitlistsResponse:
    rows = await _waiters(agent_ids)
    return WaitlistsResponse(waiters=rows, count=len(rows))


async def contention() -> ContentionResponse:
    """Per shared resource (provider, cpu, queue scope): who holds it and who waits. Install-wide."""
    holders = [c for kind in RESOURCE_KINDS for c in await _claims(None, kind)]
    waiting = [w for w in await _waiters(None) if w.scope.split(":", 1)[0] in RESOURCE_KINDS]
    scopes = sorted({c.scope for c in holders} | {w.scope for w in waiting})
    rows = []
    for scope in scopes:
        held, queued = [c for c in holders if c.scope == scope], [w for w in waiting if w.scope == scope]
        rows.append(
            ContentionRow(
                scope=scope, kind=scope.split(":", 1)[0], holders=held, waiters=queued, contended=bool(queued)
            )
        )
    return ContentionResponse(resources=rows, count=len(rows))


def _event_conflict(event: dict[str, Any], agent_ids: AgentIds) -> ConflictRow | None:
    kind = event["type"]
    if kind == RELEASE_REFUSED and _visible(agent_ids, event.get("agent_id"), event.get("holder_agent_id")):
        detail = f"release of {event.get('scope')} aimed at a claim held by another holder"
        return ConflictRow(
            at=event["at"],
            kind="release_refused",
            scope=event.get("scope"),
            agent_id=event.get("agent_id"),
            task_id=event.get("task_id"),
            other_agent_id=event.get("holder_agent_id"),
            detail=detail,
        )
    if kind == CLAIM_EXPIRED and _visible(agent_ids, event.get("agent_id")):
        detail = f"{event.get('scope')} lapsed at its TTL without being released"
        return ConflictRow(
            at=event["at"],
            kind="claim_expired",
            scope=event.get("scope"),
            agent_id=event.get("agent_id"),
            task_id=event.get("task_id"),
            detail=detail,
        )
    return None


def _yield_conflicts(events: list[dict[str, Any]], agent_ids: AgentIds) -> list[ConflictRow]:
    pending = unanswered_yields(events, now_epoch=time.time(), timeout_s=YIELD_TIMEOUT_S)
    return [
        ConflictRow(
            at=e["at"],
            kind="yield_unanswered",
            scope=e.get("scope"),
            agent_id=e.get("requester_agent_id"),
            task_id=e.get("requester_task_id"),
            detail="asked the holder to release; no answer, so the claim is held",
        )
        for e in pending
        if _visible(agent_ids, e.get("requester_agent_id"))
    ]


async def _lapsed_dev_loop() -> list[ConflictRow]:
    """Dev-loop actions that finished after their exclusive claim had lapsed (install-wide only)."""
    issues = await _read(SOURCE_DEV_LOOP, dev_loop_actions.issues_with_history)
    rows: list[ConflictRow] = []
    for issue in issues:
        for a in await _read(SOURCE_DEV_LOOP, functools.partial(dev_loop_actions.recent, issue)):
            if a.outcome == dev_loop_actions.OUTCOME_RAN_CLAIM_LAPSED:
                rows.append(
                    ConflictRow(
                        at=a.at,
                        kind="claim_lapsed_during_action",
                        scope=f"issue:{a.issue}",
                        detail=a.reason or a.intent,
                    )
                )
    return rows


async def conflicts(agent_ids: AgentIds = None) -> ConflictsResponse:
    """Refused releases, claims that expired while held, unanswered yields, lapsed dev-loop actions.

    The last needs the dev-loop history, which names no company; it is included
    only for the install-wide view (``agent_ids=None``).
    """
    events = await _read(SOURCE_EVENTS, _events)
    rows = [row for e in events if (row := _event_conflict(e, agent_ids))] + _yield_conflicts(events, agent_ids)
    if agent_ids is None:
        rows += await _lapsed_dev_loop()
    rows.sort(key=lambda r: _when(r.at), reverse=True)
    return ConflictsResponse(conflicts=rows, count=len(rows))


async def _events() -> list[dict[str, Any]]:
    client = await get_async_redis_client(database="main")
    if client is None:
        raise ClaimUnavailable("ledger events: Redis client unavailable")
    return await read_events(client)


def _when(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def _event_activity(event: dict[str, Any], agent_ids: AgentIds) -> ActivityRow | None:
    agents = (event.get("agent_id"), event.get("holder_agent_id"), event.get("requester_agent_id"))
    if not _visible(agent_ids, *agents):
        return None
    return ActivityRow(
        at=event["at"],
        type=event["type"],
        scope=event.get("scope"),
        agent_id=event.get("agent_id") or event.get("requester_agent_id"),
        task_id=event.get("task_id") or event.get("requester_task_id"),
        detail=event.get("decision") or event.get("reason") or "",
    )


async def activity(agent_ids: AgentIds = None, limit: int = EVENT_CAP) -> ActivityResponse:
    """Recent coordination activity, newest first: claims taken, queues joined, recorded events.

    Built from what the stores keep -- a held claim's ``acquired_at``, a waiter's
    ``joined_at`` and the ledger event list. A claim taken and released between
    two reads leaves no row; the registry does not keep one.
    """
    held, waiting = await _claims(agent_ids, None), await _waiters(agent_ids)
    events = await _read(SOURCE_EVENTS, _events)
    rows = [
        ActivityRow(
            at=c.acquired_at, type="claim_held", scope=c.scope, agent_id=c.agent_id, task_id=c.task_id, detail=c.intent
        )
        for c in held
    ]
    rows += [
        ActivityRow(
            at=w.joined_at, type="waiting", scope=w.scope, agent_id=w.agent_id, task_id=w.task_id, detail=w.intent
        )
        for w in waiting
    ]
    rows += [row for e in events if (row := _event_activity(e, agent_ids))]
    rows.sort(key=lambda r: _when(r.at), reverse=True)
    rows = rows[: max(limit, 1)]
    return ActivityResponse(activity=rows, count=len(rows))


async def dev_loop_issues() -> DevLoopIssuesResponse:
    issues = await _read(SOURCE_DEV_LOOP, dev_loop_actions.issues_with_history)
    return DevLoopIssuesResponse(issues=issues, count=len(issues))


async def dev_loop_history(issue: int, limit: int = dev_loop_actions.DEFAULT_RECENT_LIMIT) -> DevLoopHistoryResponse:
    actions = await _read(SOURCE_DEV_LOOP, lambda: dev_loop_actions.recent(issue, limit=limit))
    rows = [DevLoopActionRow(**dev_loop_actions.action_payload(a)) for a in actions]
    return DevLoopHistoryResponse(issue=issue, actions=rows, count=len(rows))
