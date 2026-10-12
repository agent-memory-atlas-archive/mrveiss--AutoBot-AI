# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Executes an approved request to change one AI line (#18430, #17038).

Agents never change a line. The proposer (``services.ai_line_change_proposal``) files an approval
whose context names this action; nothing is written until a PERSON approves it, and on approval
this handler is the only caller of ``llc.services.ai_lines.record_approved_line``. It REFUSES, with a
durable comment, when:

* the approval carries no company, or a malformed key or line;
* the approval was not decided by a person who is an owner or admin of THAT company -- the
  generic approval endpoint does not check company membership, so this handler must;
* the requester decided their own request while the company has two or more owners/admins (four eyes,
  owner decision #18430). A company with a single owner/admin may self-approve; that is flagged in the audit.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from autobot_shared.ai_lines import parse_line
from autobot_shared.logging_manager import get_logger
from llc.models.membership import LLCCompanyMembership
from llc.services.ai_lines import normalise_key, record_approved_line
from llc.services.authz import GRANTING_ROLES, require_company_admin
from models.approval import ApprovalComment, ApprovalStatus
from services.approval_execution import register_post_approval_action
from services.audit_logger import audit_log
from user_management.models.user import User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from models.approval import Approval

logger = get_logger(__name__)

ACTION = "ai_line.set_line"
PROPOSED_BY = "services.ai_line_change_proposal"


async def _decider_id(session: "AsyncSession", decided_by: str | None) -> uuid.UUID | None:
    """The deciding person's user id: the LLC route records a UUID, the generic route a username."""
    if not decided_by:
        return None
    try:
        return uuid.UUID(decided_by)
    except ValueError:
        found = await session.execute(select(User.id).where(User.username == decided_by))
        return found.scalar_one_or_none()


async def _admin_count(session: "AsyncSession", company_id: uuid.UUID) -> int:
    rows = await session.execute(
        select(func.count()).where(
            LLCCompanyMembership.company_id == company_id, LLCCompanyMembership.role.in_(GRANTING_ROLES)
        )
    )
    return int(rows.scalar_one())


def _requester_id(approval: "Approval") -> uuid.UUID | None:
    """The person who proposed the change, from ``requested_by_agent`` (``ai-line-change:<user id>``)."""
    try:
        return uuid.UUID(str(approval.requested_by_agent).rsplit(":", 1)[-1])
    except ValueError:
        return None


async def _four_eyes(approval: "Approval", session: "AsyncSession", decider: uuid.UUID | None) -> bool:
    """True when this is a flagged self-approval by the company's only owner/admin; raises when refused."""
    if decider is None or decider != _requester_id(approval):
        return False
    if await _admin_count(session, approval.company_id) >= 2:
        raise ValueError("a line change needs a different owner or admin than the one who asked for it")
    return True


async def _trail(
    approval: "Approval", session: "AsyncSession", body: str, result: str, self_approval: bool = False
) -> None:
    session.add(ApprovalComment(approval_id=approval.id, author="system", author_type="system", body=body))
    await session.commit()
    await audit_log(
        "ai_line.change",
        result=result,
        user_id=approval.decided_by_user,
        resource=str((approval.context or {}).get("action_key")),
        details={
            "approval_id": str(approval.id),
            "company_id": str(approval.company_id),
            "self_approval": self_approval,
        },
    )


async def execute_line_change(approval: "Approval", session: "AsyncSession") -> None:
    """Apply the approved line. Raises (recorded by the dispatcher) when it must not."""
    ctx = approval.context or {}
    line = parse_line(ctx.get("line"))
    if approval.status != ApprovalStatus.APPROVED.value or approval.company_id is None or line is None:
        raise ValueError("not an approved, company-scoped request naming one of the three lines")
    key = normalise_key(str(ctx.get("action_key", "")))
    decider = await _decider_id(session, approval.decided_by_user)
    await require_company_admin(session, approval.company_id, decider)
    self_approval = await _four_eyes(approval, session, decider)
    await record_approved_line(
        session,
        company_id=approval.company_id,
        key=key,
        line=line,
        approval_id=approval.id,
        decided_by=str(approval.decided_by_user),
    )
    note = " (self-approved: the company's only owner/admin)" if self_approval else ""
    await _trail(approval, session, f"AI line for '{key}' is now {line.value}{note}.", "success", self_approval)


def register_ai_line_change_action() -> None:
    """Register the handler; called when the approval-gates router loads, before any approve()."""
    register_post_approval_action(ACTION, execute_line_change, proposed_by=PROPOSED_BY)
