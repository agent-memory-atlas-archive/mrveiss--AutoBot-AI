# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The agent terminal's call into the single AI-line enforcement point (#18430, #17463).

The role policy ``ApprovalHandler.needs_approval`` (``auto_approve_safe`` / ``auto_approve_moderate``) is an
INPUT to the point that can only tighten: a role that needs approval for a risk can never be line 1.
Line 2 holds the command in this terminal's own approval queue, so the point is told not to file a second
approval for it.
"""

from __future__ import annotations

from services.ai_line_enforcement import LineVerdict, Outcome, enforce_line

from .approval_handler import ApprovalHandler
from .models import AgentTerminalSession

__all__ = ["Outcome", "resolve_command_line"]

#: The tool name the terminal's commands carry in the tool-effect catalogue.
TERMINAL_TOOL = "execute_command"


async def resolve_command_line(
    approval_handler: ApprovalHandler, session: AgentTerminalSession, command: str, risk
) -> LineVerdict:
    """Resolve *command*'s line for this session's company (its tenant) and agent role."""
    return await enforce_line(
        tool_name=TERMINAL_TOOL,
        params={"command": command},
        company_id=session.tenant_id,
        requested_by=session.agent_id,
        surface="agent_terminal",
        role_requires_approval=approval_handler.needs_approval(session.agent_role, risk),
        hold_in_approvals=False,
    )
