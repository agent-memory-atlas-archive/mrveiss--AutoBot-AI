# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""list_pending against a real migrated schema: one inbox across every source (#13421 S1)."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from models.approval import Approval
from services import approval_execution
from services.approval_gate_service import ApprovalGateService
from tests.migrations.conftest import requires_postgres, run_alembic

pytestmark = [pytest.mark.migration_gate, requires_postgres]

_LIMIT = 100  # larger than any row count seeded here


async def _service(url: str):
    engine = create_async_engine(url)
    return engine, ApprovalGateService(AsyncSession(engine, expire_on_commit=False))


async def test_pending_spans_sources_and_excludes_decided_and_company_rows(fresh_db_url, monkeypatch):
    assert run_alembic(["upgrade", "head"], fresh_db_url).returncode == 0
    monkeypatch.setattr(ApprovalGateService, "_notify", lambda self, e, a: _none())
    engine, svc = await _service(fresh_db_url)
    try:
        legacy = await svc.create_approval(title="legacy", approval_type="workflow_gate")
        tool = await svc.create_approval(
            title="tool", approval_type="destructive_action", source="agent_tool", risk_tier="critical"
        )
        plugin = await svc.create_approval(
            title="plugin", approval_type="resource_request", source="plugin_capability", risk_tier="low"
        )
        decided = await svc.create_approval(
            title="done", approval_type="workflow_gate", source="agent_tool", risk_tier="low"
        )
        await svc.reject(decided.id, "alice", author_type="human")
        svc.session.add(Approval(title="llc", approval_type="hire", status="pending", company_id=uuid.uuid4()))
        await svc.session.commit()

        every = {a.id for a in await svc.list_pending(limit=_LIMIT, offset=0)}
        assert every == {legacy.id, tool.id, plugin.id}
        assert {a.id for a in await svc.list_pending(limit=_LIMIT, offset=0, source="agent_tool")} == {tool.id}
        assert {a.id for a in await svc.list_pending(limit=_LIMIT, offset=0, risk_tier="low")} == {plugin.id}
        assert {
            a.id for a in await svc.list_pending(limit=_LIMIT, offset=0, source="agent_tool", risk_tier="low")
        } == set()
    finally:
        await svc.session.close()
        await engine.dispose()


async def test_a_reject_resumes_the_source_through_the_real_table(fresh_db_url, monkeypatch):
    assert run_alembic(["upgrade", "head"], fresh_db_url).returncode == 0
    monkeypatch.setattr(ApprovalGateService, "_notify", lambda self, e, a: _none())
    seen = []

    async def handler(approval, session):
        seen.append((approval.status, approval.resume_ref))

    monkeypatch.setattr(approval_execution, "_RESUME_HANDLERS", {"agent_tool": handler})
    engine, svc = await _service(fresh_db_url)
    try:
        row = await svc.create_approval(
            title="t", approval_type="workflow_gate", source="agent_tool", risk_tier="high", resume_ref="call-9"
        )
        await svc.reject(row.id, "alice", author_type="human")
        assert seen == [("rejected", "call-9")]
    finally:
        await svc.session.close()
        await engine.dispose()


async def _none():
    return None
