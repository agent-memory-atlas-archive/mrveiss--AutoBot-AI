# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Canonical audit service -- the one write and read path for every audit record.

#6475 opened with three audit stores and closed with them still live; by #17225
there were eight. Every one now funnels here (docs/developer/AUDIT_SYSTEMS.md):

* ``services/audit/audit_log.py``, ``services/event_log.py`` and
  ``knowledge/audit_log.py`` are retired (D2); their vocabularies live in
  ``taxonomy.py`` and their callers use the helpers below.
* ``services/audit_logger.audit_log()`` is an adapter onto :func:`record` (D3).
* ``SecurityLayer.audit_log`` routes through :func:`emit_security` (D6).
* Retention comes from one table, ``retention.py`` (D5).

Every producer -- ``emit``, ``emit_security``, ``emit_compliance``,
``emit_knowledge``, ``audit_record`` -- reaches :func:`record`.
"""

import asyncio
import time
from typing import Any, Dict, List

from autobot_shared.async_compat import fire_and_forget_threadsafe
from autobot_shared.logging_manager import get_logger
from autobot_shared.redis_client import get_async_redis_client
from autobot_shared.redis_write import run_redis_write
from services.audit.projection import count_events, read_events, write_event
from services.audit.purge import PurgeResult, purge_expired
from services.audit.taxonomy import (  # noqa: F401 -- re-exported vocabulary
    AuditAction,
    AuditCategory,
    AuditEvent,
    AuditEventType,
    AuditFilter,
    EventType,
    action_name,
)

logger = get_logger(__name__)

#: Upper bound on members scanned per query before in-memory filtering (#8302).
DEFAULT_MAX_SCAN = 10_000

#: The serving loop, captured the first time a record is made on it, so a
#: producer on a thread with no loop of its own can hand its write over.
_LOOP: asyncio.AbstractEventLoop | None = None


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


async def _analyse_for_threats(event: AuditEvent) -> None:
    """Hand one audit event to the threat detection engine (#13562).

    Imported inside the function on purpose. The engine's module imports
    scikit-learn at top level, so a module-scope import here would make the
    audit bus — which every router touches — fail to import wherever
    scikit-learn is absent.
    """
    try:
        from security.enterprise.threat_detection.ingest import analyze_audit_event

        await analyze_audit_event(event.to_dict())
    except Exception as exc:  # noqa: BLE001 — the audit write must survive it
        logger.error("Threat analysis could not run for %s: %s", event.action, type(exc).__name__)


async def record(event: AuditEvent) -> None:
    """Persist one audit event.

    #13562: this is also where the bus feeds ``ThreatDetectionEngine``. Analysis
    runs BEFORE the write and never depends on it: a detector that only sees the
    events storage accepted is blindest exactly when the platform is degraded.
    """
    _remember_loop()
    await _analyse_for_threats(event)
    redis = await get_async_redis_client(database="main")
    if redis is None:
        logger.error("audit: Redis unavailable, event dropped: %s/%s", event.category.value, event.action)
        return
    await write_event(redis, event)


def _remember_loop() -> asyncio.AbstractEventLoop | None:
    """The loop running in this thread, remembered for producers on other threads."""
    global _LOOP
    try:
        _LOOP = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return _LOOP


def emit(event: AuditEvent) -> bool:
    """Schedule :func:`record` without waiting; safe from any thread.

    Returns ``False`` only when nothing could be scheduled -- a thread with no
    running loop before any loop was seen -- which is logged at ERROR. A sync
    FastAPI handler or a worker thread hands the write to the serving loop.

    #17225: this name used to be redefined further down as an ``EventType``
    bridge, so ``emit(AuditEvent(...))`` -- the call every direct producer made
    -- was recorded as a COMPLIANCE event whose action was the event's repr.
    """
    if _remember_loop() is None:
        return fire_and_forget_threadsafe(record(event), name="audit", loop=_LOOP) is not None
    run_redis_write(record(event), label="audit")
    return True


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


class AuditStoreUnavailable(RuntimeError):
    """The audit trail could not be read -- distinct from "nothing was recorded"."""


async def query_filtered(
    flt: AuditFilter, limit: int = 100, offset: int = 0, max_scan: int = DEFAULT_MAX_SCAN
) -> List[Dict[str, Any]]:
    """Every event matching *flt*, newest first, paginated.

    Raises :class:`AuditStoreUnavailable` when the store cannot be reached: an
    unreadable audit trail must never read as an empty one (#13258).
    """
    redis = await get_async_redis_client(database="main")
    if redis is None:
        raise AuditStoreUnavailable("the audit trail cannot be read: Redis is unavailable")
    events = await read_events(redis, flt, max_scan)
    return events[offset : offset + limit]


async def query(
    category: AuditCategory | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    from_ts: float | None = None,
    to_ts: float | None = None,
    limit: int = 100,
    offset: int = 0,
    max_scan: int = DEFAULT_MAX_SCAN,
) -> List[Dict[str, Any]]:
    """Query audit events; "everything for user U in window W" is ``query(actor_id=U, ...)``."""
    flt = AuditFilter(category=category, actor_id=actor_id, action=action, from_ts=from_ts, to_ts=to_ts)
    return await query_filtered(flt, limit=limit, offset=offset, max_scan=max_scan)


async def query_events(
    user_id: str | None = None,
    event_type: str | None = None,
    from_ts: float | None = None,
    to_ts: float | None = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Compliance events (the admin event-log view; was ``services.event_log.query_events``)."""
    return await query(
        category=AuditCategory.COMPLIANCE,
        actor_id=user_id,
        action=event_type,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
        offset=offset,
    )


async def count_since(from_ts: float) -> int | None:
    """Events recorded since *from_ts*; ``None`` when the store cannot be reached."""
    redis = await get_async_redis_client(database="main")
    if redis is None:
        return None
    return await count_events(redis, from_ts)


async def apply_retention(cap_days: int | None = None, dry_run: bool = False) -> PurgeResult:
    """Apply the retention table (``retention.py``) -- the one purge path (D5)."""
    redis = await get_async_redis_client(database="main")
    if redis is None:
        logger.warning("audit: Redis unavailable, retention not applied")
        return PurgeResult(errors=1)
    return await purge_expired(redis, time.time(), cap_days=cap_days, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Category helpers -- the producers' entry points
# ---------------------------------------------------------------------------


def emit_compliance(
    action: Any,
    actor_id: str | None,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: Dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> bool:
    """Record a COMPLIANCE event; *action* may be an ``EventType`` or a string."""
    return emit(
        AuditEvent(
            category=AuditCategory.COMPLIANCE,
            action=action_name(action),
            actor_id=actor_id or "system",
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata or {},
            ip_address=ip_address,
        )
    )


def emit_security(
    action: Any,
    actor_id: str,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: Dict[str, Any] | None = None,
    ip_address: str | None = None,
    session_id: str | None = None,
    outcome: str = "success",
) -> bool:
    """Record a SECURITY event; *action* may be an ``AuditAction`` or a string."""
    return emit(
        AuditEvent(
            category=AuditCategory.SECURITY,
            action=action_name(action),
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata or {},
            ip_address=ip_address,
            session_id=session_id,
            outcome=outcome,
        )
    )


async def emit_knowledge(
    action: Any,
    actor_id: str,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: Dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    """Record a KNOWLEDGE event and wait for it; *action* may be an ``AuditEventType``."""
    await record(
        AuditEvent(
            category=AuditCategory.KNOWLEDGE,
            action=action_name(action),
            actor_id=actor_id,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata or {},
            ip_address=ip_address,
        )
    )


def audit_record(
    user_id: str,
    action: Any,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: Dict[str, Any] | None = None,
    ip_address: str | None = None,
    session_id: str | None = None,
    outcome: str = "success",
) -> None:
    """Positional-signature form of :func:`emit_security` (was ``audit_log.audit_record``)."""
    emit_security(
        action,
        user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata,
        ip_address=ip_address,
        session_id=session_id,
        outcome=outcome,
    )
