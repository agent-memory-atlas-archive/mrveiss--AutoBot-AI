# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The two named postures of the one session-ownership validator (#14016).

``security.session_ownership.validate_session_ownership`` is the only validator. It takes a
``SessionPosture``:

* ``DEFAULT`` -- the flag-aware chat posture: auth-disabled and enforcement-mode fast paths,
  ``log_only`` rollout, org-admin and shared-session grants, legacy claim of an unowned session.
* ``STRICT`` -- the conversation-file posture, which never consults a flag: a role is required,
  a global admin is allowed, everyone else must be the recorded owner, and there are no shared or
  org grants. An unowned (pre-ownership) session is allowed and is NOT claimed.

Any failure to look the owner up denies under both postures (#14033), with 403 and nothing claimed.

Its own module because ``session_ownership.py`` is at its recorded size ceiling (#14236).
"""

from __future__ import annotations

import enum
from typing import Dict

from fastapi import HTTPException, Request

from auth_middleware import get_auth_middleware
from autobot_shared.auth.permissions import is_admin_role
from autobot_shared.logging_manager import get_logger
from utils.catalog_http_exceptions import raise_internal_error

logger = get_logger(__name__)


class SessionPosture(enum.Enum):
    """Which rules ``validate_session_ownership`` applies."""

    DEFAULT = "default"
    STRICT = "strict"


def authenticated_user(session_id: str, request: Request) -> Dict:
    """The authenticated user from the request, or 401."""
    user_data = get_auth_middleware().get_user_from_request(request)
    if not user_data:
        logger.warning("Unauthenticated access attempt to session %s...", session_id[:8])
        raise HTTPException(status_code=401, detail="Authentication required to access conversations")
    return user_data


def deny_unreadable_owner(session_id: str, username: str | None) -> HTTPException:
    """The one denial for an owner that cannot be read: not evidence of "unowned" (#14033)."""
    logger.warning("Ownership unreadable for session %s... - denying %s", session_id[:8], username)
    return HTTPException(status_code=403, detail="Cannot verify session ownership")


async def _strict_owner_check(session_id: str, user_data: Dict, request: Request) -> Dict:
    """Admin passes; otherwise the recorded owner must be the caller. Unowned is legacy-allowed."""
    from utils.chat_utils import get_chat_history_manager  # noqa: PLC0415

    username = user_data.get("username")
    try:
        owner = await get_chat_history_manager(request).get_session_owner(session_id)
    except Exception:  # any lookup failure, typed (SessionOwnerUnreadable) or not, is an unknown owner
        raise deny_unreadable_owner(session_id, username) from None
    if owner is None:
        logger.info("Session %s... has no owner - allowing access (legacy session)", session_id[:8])
        return {"authorized": True, "user_data": user_data, "reason": "legacy_unowned"}
    if owner != username:
        logger.warning("Access denied: %s wanted session %s... owned by %s", username, session_id[:8], owner)
        raise HTTPException(status_code=403, detail="Access denied: You do not own this session")
    return {"authorized": True, "user_data": user_data, "reason": "owner_match"}


async def validate_strict(session_id: str, user_data: Dict, request: Request) -> Dict:
    """The STRICT posture. Always enforces; reads no flag, grants nothing by share or org."""
    try:
        if not user_data.get("role"):
            raise HTTPException(status_code=403, detail="User role not assigned - access denied")
        if is_admin_role(user_data.get("role")):
            return {"authorized": True, "user_data": user_data, "reason": "admin"}
        return await _strict_owner_check(session_id, user_data, request)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Session ownership validation error: %s", e)
        raise_internal_error("Failed to validate session ownership")
