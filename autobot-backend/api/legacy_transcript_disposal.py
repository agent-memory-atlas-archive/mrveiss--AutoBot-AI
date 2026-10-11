# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""What a session delete may do to the session's legacy transcript (#18293, #17038).

``DELETE /chat/sessions/{id}`` used to remove the transcript before it knew the
session existed, and ``validate_session_ownership`` hands an ownerless session to
whoever asks (``reason="legacy_migration"``) -- so any user could delete another
user's legacy transcript by id. Now:

* the chat session is deleted FIRST; a session that does not exist in
  ``chat_sessions`` is a 404 and nothing else is touched
  (:func:`delete_session_or_refer`);
* the transcript is removed only when the caller is the session's VERIFIED owner:
  ownership resolved as ``owner_match`` AND the durable owner recorded on the
  session itself is the caller (:func:`is_verified_owner`). A Redis-only owner
  record is not enough -- a claim of an unowned session writes one;
* every other case leaves the file on disk and raises a #17038 proposal
  (:func:`propose_transcript_disposal`), executed only if a human approves
  (``services/legacy_transcript_disposal_action.py``).
"""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select

from autobot_shared.logging_manager import get_logger
from chat_history.legacy_transcripts import legacy_transcript_file, remove_legacy_transcript
from services.legacy_transcript_disposal_action import ACTION, register_disposal_action

logger = get_logger(__name__)

# Registered at import, like api/admin_orphan_storage.py: the handler must be in
# place before any approve() can reach a proposal this module raised.
register_disposal_action()


def is_verified_owner(ownership: Dict[str, Any] | None, durable_owner: str | None) -> bool:
    """True only for ``owner_match`` by the user the session file itself names as owner."""
    ownership = ownership or {}
    username = (ownership.get("user_data") or {}).get("username")
    return ownership.get("reason") == "owner_match" and bool(username) and durable_owner == username


async def read_durable_owner(chat_history_manager, session_id: str) -> str | None:
    """The owner recorded on the session file, read BEFORE the session is deleted."""
    try:
        owner = await chat_history_manager.get_session_owner(session_id)
    except Exception as exc:
        logger.warning("Could not read the durable owner of session %s: %s", session_id, exc)
        return None
    return owner if isinstance(owner, str) and owner else None


async def _pending_proposal_id(db, session_id: str) -> str | None:
    from models.approval import Approval, ApprovalStatus

    query = select(Approval.id).where(
        Approval.status == ApprovalStatus.PENDING.value,
        Approval.context["action"].astext == ACTION,
        Approval.context["session_id"].astext == session_id,
    )
    found = (await db.execute(query)).scalars().first()
    return str(found) if found else None


async def propose_transcript_disposal(session_id: str, requested_by: str | None, why: str) -> str:
    """Raise (or reuse the pending) #17038 proposal to remove one legacy transcript."""
    from models.approval import ApprovalType
    from services.approval_gate_service import ApprovalGateService
    from user_management.database import get_async_session_factory

    async with get_async_session_factory()() as db:
        existing = await _pending_proposal_id(db, session_id)
        if existing:
            return existing
        approval = await ApprovalGateService(db).create_approval(
            title=f"Remove legacy chat transcript: {session_id}",
            approval_type=ApprovalType.DESTRUCTIVE_ACTION.value,
            description=why,
            requested_by_agent=requested_by,
            context={"action": ACTION, "session_id": session_id},
        )
    return str(approval.id)


async def _refer(session_id: str, requested_by: str | None, why: str) -> str | None:
    """Propose disposal; a failure to propose leaves the file and is reported, never raised."""
    try:
        return await propose_transcript_disposal(session_id, requested_by, why)
    except Exception as exc:
        logger.error("Could not record a #17038 proposal for session %s's transcript: %s", session_id, exc)
        return None


async def dispose_session_transcript(session_id: str, *, owner_verified: bool, requested_by: str | None) -> dict:
    """Remove the transcript for its verified owner; otherwise leave it and propose."""
    if legacy_transcript_file(session_id) is None:
        return {"transcript_deleted": False, "proposal_id": None, "error": None}
    if owner_verified:
        return {**await remove_legacy_transcript(session_id), "proposal_id": None}
    why = "Session deleted by a caller who is not its recorded owner; the legacy transcript was kept."
    proposal_id = await _refer(session_id, requested_by, why)
    return {"transcript_deleted": False, "proposal_id": proposal_id, "error": None}


async def delete_session_or_refer(chat_history_manager, session_id: str, requested_by: str | None) -> None:
    """Delete the chat session, or raise 404 -- referring a legacy-only transcript for review."""
    from exceptions import get_exceptions_lazy

    if await chat_history_manager.delete_session(session_id):
        return
    message = f"Session {session_id} not found"
    if legacy_transcript_file(session_id) is not None:
        why = "Delete requested for a session that exists only as a legacy transcript."
        if proposal_id := await _refer(session_id, requested_by, why):
            message += f"; its legacy transcript was referred for review (approval {proposal_id})"
    resource_not_found = get_exceptions_lazy()[2]
    raise resource_not_found(message)
