# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""PROPOSE a change to an AI line; never apply it (#18430, #17038).

Changing a line is itself a decision for a person, so it goes through Approvals: this files one
company-scoped approval whose context names ``ai_line.set_line``. Nothing in this module writes the
line store, and an already-pending request for the same company, action and line is reused.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from autobot_shared.ai_lines import AiLine
from autobot_shared.logging_manager import get_logger
from llc.services.ai_lines import normalise_key
from models.approval import Approval, ApprovalStatus, ApprovalType
from services.ai_line_change_action import ACTION
from services.approval_gate_service import ApprovalGateService
from user_management.database import get_async_session_factory

logger = get_logger(__name__)

REQUESTER_PREFIX = "ai-line-change"


async def propose_line_change(
    *, company_id: uuid.UUID, key: str, line: AiLine, requested_by: str, reason: str | None = None
) -> Approval:
    """File (or reuse) the pending request to set *key* to *line* for *company_id*."""
    key = normalise_key(key)
    async with get_async_session_factory()() as session:
        pending = await session.execute(
            select(Approval).where(
                Approval.company_id == company_id,
                Approval.status == ApprovalStatus.PENDING.value,
                Approval.approval_type == ApprovalType.WORKFLOW_GATE.value,
                Approval.requested_by_agent == f"{REQUESTER_PREFIX}:{requested_by}",
            )
        )
        for existing in pending.scalars().all():
            ctx = existing.context or {}
            if ctx.get("action") == ACTION and ctx.get("action_key") == key and ctx.get("line") == line.value:
                return existing
        return await ApprovalGateService(session).create_approval(
            title=f"Set the AI line for '{key}' to {line.value}",
            approval_type=ApprovalType.WORKFLOW_GATE.value,
            requested_by_agent=f"{REQUESTER_PREFIX}:{requested_by}",
            description=reason or f"Change the line for '{key}' to {line.value}. Nothing changes until approved.",
            context={"action": ACTION, "action_key": key, "line": line.value},
            company_id=company_id,
        )
