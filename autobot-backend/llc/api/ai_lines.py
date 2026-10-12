# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The three AI lines per tool action (#18430).

Route group: ``/llc/ai-lines/{company_id}``

* ``GET  /{company_id}``          -- every action's line, the effective line, and why.
* ``POST /{company_id}/changes``  -- PROPOSE a change. It files an approval and changes nothing:
  the line is written only when a person approves it (``services.ai_line_change_action``).

Both are company-scoped (``assert_company_access``). Proposing is refused for anything but a
person's interactive login -- an agent never changes a line.
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.user_management.dependencies import get_current_user, require_org_context
from api.user_management.human_decider import require_interactive_human
from autobot_shared.ai_lines import DEFAULT_LINE, AiLine
from llc.deps import assert_company_access, get_session
from llc.services.ai_lines import normalise_key
from services.ai_line_change_action import register_ai_line_change_action
from services.ai_line_change_proposal import propose_line_change
from services.ai_line_view import describe_lines
from user_management.services import TenantContext

from ._common import actor_id

router = APIRouter(prefix="/ai-lines", tags=["llc-ai-lines"])
register_ai_line_change_action()  # the handler exists before any approval can name it


class AiLineEntry(BaseModel):
    """One tool action: the stored line (if any), what it really resolves to, and why."""

    action_key: str
    stored_line: Optional[AiLine] = None
    effective_line: AiLine
    is_set: bool
    reason: str
    floor_category: Optional[str] = None
    approval_id: Optional[str] = None
    decided_by: Optional[str] = None


class AiLinesResponse(BaseModel):
    company_id: str
    default_line: AiLine
    entries: List[AiLineEntry]


class ProposeLineChange(BaseModel):
    action_key: str = Field(min_length=1, max_length=320, description="'<tool>' or '<tool>:<action>'")
    line: AiLine
    reason: Optional[str] = Field(default=None, max_length=1000)


class ProposedLineChange(BaseModel):
    approval_id: str
    status: str
    action_key: str
    line: AiLine


@router.get("/{company_id}", response_model=AiLinesResponse)
async def read_lines(
    company_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _current_user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(require_org_context),
) -> AiLinesResponse:
    assert_company_access(ctx, company_id)
    views = await describe_lines(session, company_id)
    return AiLinesResponse(
        company_id=str(company_id),
        default_line=DEFAULT_LINE,
        entries=[
            AiLineEntry(
                action_key=v.action_key,
                stored_line=v.stored,
                effective_line=v.resolution.line,
                is_set=v.stored is not None,
                reason=v.resolution.reason,
                floor_category=v.resolution.floor_category,
                approval_id=str(v.approval_id) if v.approval_id else None,
                decided_by=v.decided_by,
            )
            for v in views
        ],
    )


@router.post("/{company_id}/changes", response_model=ProposedLineChange, status_code=202)
async def propose_change(
    company_id: uuid.UUID,
    payload: ProposeLineChange,
    current_user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(require_org_context),
) -> ProposedLineChange:
    require_interactive_human(current_user, "AI line change request")
    assert_company_access(ctx, company_id)
    try:
        key = normalise_key(payload.action_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    approval = await propose_line_change(
        company_id=company_id,
        key=key,
        line=payload.line,
        requested_by=str(actor_id(current_user)),
        reason=payload.reason,
    )
    return ProposedLineChange(approval_id=str(approval.id), status=approval.status, action_key=key, line=payload.line)
