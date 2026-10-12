# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Bounds on the two anonymous RUM ingest routes, ``/event`` and ``/metrics`` (#16375).

Those routes are open by design (see ``api/rum.py``): the browser's RumAgent
sends a bare ``fetch``/``sendBeacon`` with no credential, from every page
including ``/login`` and ``/shared/:token``. An open write must not be a free
amplifier, so each request is held to the #17007 treatment:

* **rate-limited** per client address with the shared ``IPRateLimiter``
  (``AUTOBOT_RUM_INGEST_RATE_LIMIT``); over it, 429 and nothing is written;
* **size-capped** before the payload is used (``AUTOBOT_RUM_*_MAX_BYTES``), 413;
* **field-capped**: each identifying string has a maximum length, 422;
* **one bounded row**: an event appends one entry to a capped buffer and
  upserts one entry in a capped session table -- the oldest is evicted, so no
  number of requests grows either past its cap.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException, Request

from autobot_shared.env_registry import REGISTRY, env
from autobot_shared.proxy_utils import get_client_ip
from autobot_shared.rate_limit import IPRateLimiter

_RATE_ENV = "AUTOBOT_RUM_INGEST_RATE_LIMIT"

_ingest_limiter = IPRateLimiter(
    key_prefix="ratelimit:rum_ingest",
    limit_env=_RATE_ENV,
    default_limit=REGISTRY[_RATE_ENV].default,
    window_seconds=60,
)

#: Longest accepted value of each top-level ``RumEvent`` string. The body cap
#: bounds ``data``; these stop the identifying fields carrying the payload.
EVENT_FIELD_CAPS: Dict[str, int] = {
    "type": 64,
    "timestamp": 64,
    "sessionId": 128,
    "url": 2048,
    "userAgent": 512,
}


def _too_large(cap: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"RUM payload exceeds the {cap}-byte limit",
    )


async def _reject_oversize(request: Request, cap: int) -> None:
    """413 when the declared or actual body is over *cap* bytes."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise _too_large(cap)
    if len(await request.body()) > cap:
        raise _too_large(cap)


async def _bound(request: Request, cap_env: str) -> None:
    await _ingest_limiter.check_or_429(get_client_ip(request) or "unknown")
    await _reject_oversize(request, int(env(cap_env)))


async def bound_event_ingest(request: Request) -> None:
    """Route dependency for ``POST /rum/event``: rate limit, then size cap."""
    await _bound(request, "AUTOBOT_RUM_EVENT_MAX_BYTES")


async def bound_metrics_ingest(request: Request) -> None:
    """Route dependency for ``POST /rum/metrics``: rate limit, then size cap."""
    await _bound(request, "AUTOBOT_RUM_METRICS_MAX_BYTES")


def enforce_event_field_caps(event: Any) -> None:
    """422 when an identifying ``RumEvent`` string is longer than its cap."""
    for name, cap in EVENT_FIELD_CAPS.items():
        if len(getattr(event, name, "") or "") > cap:
            raise HTTPException(
                status_code=422,
                detail=f"RUM event field '{name}' exceeds {cap} characters",
            )


def append_bounded(events: List[dict], event_data: dict) -> None:
    """Append one event, dropping the oldest beyond ``AUTOBOT_RUM_MAX_EVENTS``."""
    events.append(event_data)
    overflow = len(events) - int(env("AUTOBOT_RUM_MAX_EVENTS"))
    if overflow > 0:
        del events[:overflow]


def track_session(sessions: Dict[str, dict], event_data: dict) -> int:
    """Upsert the event's session, evicting the oldest beyond the cap; returns its count."""
    session_id = event_data["sessionId"]
    session = sessions.get(session_id)
    if session is None:
        session = sessions[session_id] = {
            "start_time": event_data["server_timestamp"],
            "event_count": 0,
            "user_agent": event_data["userAgent"],
            "initial_url": event_data["url"],
        }
        while len(sessions) > int(env("AUTOBOT_RUM_MAX_SESSIONS")):
            sessions.pop(next(iter(sessions)))
    session["event_count"] += 1
    session["last_activity"] = event_data["server_timestamp"]
    return session["event_count"]
