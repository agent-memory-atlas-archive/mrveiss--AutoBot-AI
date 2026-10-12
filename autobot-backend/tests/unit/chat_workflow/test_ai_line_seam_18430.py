# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The chat tool seam resolves each tool ACTION's AI line (#18430).

Same call, different line, different outcome: line 1 passes through, line 2 is held with a pending
approval, line 3 is refused. The contrasts are in the same test so a seam that ignored the line would
fail on one side of each.
"""

from types import SimpleNamespace

import pytest

from autobot_shared.ai_lines import AiLine
from tests.unit.chat_workflow.test_ai_line_support import arun_guard

L1, L2, L3 = AiLine.AUTONOMOUS, AiLine.DRAFT_THEN_SEND, AiLine.NEVER
RELAXED = ["pushing commits", "sending externally"]


def _ctx(**kw) -> SimpleNamespace:
    base = {"requires_approval_before": [], "work_item_id": None, "context": {"company_id": "co-1"}, "session_id": "s"}
    return SimpleNamespace(**{**base, **kw})


@pytest.mark.asyncio
async def test_each_action_of_one_tool_gets_its_own_line():
    lines = {"crm:read": L1, "crm:send": L2, "crm:delete": L3}
    outcomes = {}
    for action in ("read", "send", "delete"):
        results: list = []
        message, filed = await arun_guard({"name": "crm", "params": {"action": action}}, _ctx(), results, stored=lines)
        outcomes[action] = (message and message.type, filed, results and results[0]["status"])
    assert outcomes == {
        "read": (None, 0, []),
        "send": ("approval_required", 1, "pending_approval"),
        "delete": ("error", 0, "error"),
    }


@pytest.mark.asyncio
async def test_a_refusal_carries_the_line_and_is_not_an_approval_request():
    results: list = []
    message, filed = await arun_guard(
        {"name": "crm", "params": {"action": "delete"}}, _ctx(), results, stored={"crm:delete": L3}
    )
    assert message.metadata["error"] is True and message.metadata["approval_required"] is False
    assert message.metadata["line"] == "never" and filed == 0 and "never touches" in message.content


@pytest.mark.asyncio
async def test_a_held_call_reports_its_approval_so_the_person_can_find_it():
    message, _ = await arun_guard({"name": "crm", "params": {"action": "send"}}, _ctx(), [], stored={"crm:send": L2})
    assert message.metadata["approval_id"] and message.metadata["line"] == "draft_then_send"


@pytest.mark.asyncio
async def test_a_persons_yes_in_the_interrupt_satisfies_line_two_and_never_line_three():
    lines = {"crm:send": L2, "crm:delete": L3}
    send = {"name": "crm", "params": {"action": "send"}}
    delete = {"name": "crm", "params": {"action": "delete"}}
    held, _ = await arun_guard(send, _ctx(), [], stored=lines)
    passed, _ = await arun_guard(send, _ctx(person_approved=True), [], stored=lines)
    refused, _ = await arun_guard(delete, _ctx(person_approved=True), [], stored=lines)
    assert (held.type, passed, refused.type) == ("approval_required", None, "error")


@pytest.mark.asyncio
async def test_a_request_cannot_satisfy_line_two_through_its_own_context():
    ctx = _ctx(context={"company_id": "co-1", "person_approved": True, "ai_line_person_approved": True})
    message, _ = await arun_guard({"name": "crm", "params": {"action": "send"}}, ctx, [], stored={"crm:send": L2})
    assert message is not None and message.type == "approval_required"


@pytest.mark.asyncio
async def test_a_work_item_category_still_holds_a_tool_the_company_set_to_line_one():
    ctx = _ctx(requires_approval_before=["pushing commits"], work_item_id="wi-1")
    held, _ = await arun_guard({"name": "git_push"}, ctx, [], stored=L1, slm_relaxed=RELAXED)
    free, _ = await arun_guard({"name": "git_push"}, _ctx(), [], stored=L1, slm_relaxed=RELAXED)
    assert held is not None and held.metadata["category"] == "pushing commits"
    assert free is None  # contrast: no declaration, category relaxed, line 1 set -> it runs


@pytest.mark.asyncio
async def test_an_unreadable_store_refuses_rather_than_runs():
    message, filed = await arun_guard({"name": "crm", "params": {"action": "read"}}, _ctx(), [], store_unavailable=True)
    assert message.type == "error" and filed == 0
