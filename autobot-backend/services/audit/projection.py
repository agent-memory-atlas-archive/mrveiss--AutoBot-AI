# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The Redis projection of the audit trail (#17225).

``audit:unified`` holds every event; ``audit:category:<category>`` holds one
category each, with the TTL the retention table (``retention.py``) assigns it.
This is the ONLY code that writes an ``audit:`` key in the backend's main Redis
database -- the guard ``repo_tests/audit_writer_single_source_17225_test.py``
fails on a second one.
"""

import json
from typing import Any, Dict, List

from autobot_shared.store_authority import system_of_record
from services.audit.retention import ttl_seconds
from services.audit.taxonomy import AuditCategory, AuditEvent, AuditFilter

# #17225 D1: declares where the audit trail is durable -- see autobot_shared/store_authority.py.
SYSTEM_OF_RECORD = system_of_record("audit_trail")

UNIFIED_KEY = "audit:unified"
_CATEGORY_KEY_PREFIX = "audit:category:"


def category_key(category: AuditCategory) -> str:
    """The per-category projection key."""
    return f"{_CATEGORY_KEY_PREFIX}{category.value}"


async def write_event(redis: Any, event: AuditEvent) -> None:
    """Project one event onto the unified and per-category sorted sets."""
    raw = json.dumps(event.to_dict(), ensure_ascii=False)
    cat_key = category_key(event.category)
    async with redis.pipeline() as pipe:
        await pipe.zadd(UNIFIED_KEY, {raw: event.occurred_at})
        # UNIFIED_KEY spans categories with different retention -- never expire
        # it (#8303); the nightly purge trims it at the longest period.
        await pipe.zadd(cat_key, {raw: event.occurred_at})
        await pipe.expire(cat_key, ttl_seconds(event.category))
        await pipe.execute()


def _decode(raws: List[Any], flt: AuditFilter) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for raw in raws:
        try:
            ev = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(ev, dict) and flt.matches(ev):
            events.append(ev)
    return events


async def read_events(redis: Any, flt: AuditFilter, max_scan: int) -> List[Dict[str, Any]]:
    """Events matching *flt*, newest first. Scans at most *max_scan* members (#8302)."""
    key = category_key(flt.category) if flt.category else UNIFIED_KEY
    low: Any = flt.from_ts if flt.from_ts is not None else 0
    high: Any = flt.to_ts if flt.to_ts is not None else "+inf"
    raws = await redis.zrevrangebyscore(key, high, low, start=0, num=max_scan)
    events = _decode(raws, flt)
    events.sort(key=lambda e: e.get("occurred_at", 0), reverse=True)
    return events


async def count_events(redis: Any, from_ts: float) -> int:
    """Events of every category recorded at or after *from_ts*."""
    return int(await redis.zcount(UNIFIED_KEY, from_ts, "+inf"))
