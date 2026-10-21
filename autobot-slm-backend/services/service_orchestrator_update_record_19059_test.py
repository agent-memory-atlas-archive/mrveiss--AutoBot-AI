# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""``ServiceOrchestrator._update_service_record`` goes through the shared upsert core (#19059)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.service_categorizer import categorize_service
from services.service_orchestrator import ServiceOrchestrator

_NODE = "node-1"


def _db(existing=None) -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


def _orchestrator() -> ServiceOrchestrator:
    return ServiceOrchestrator.__new__(ServiceOrchestrator)


@pytest.mark.asyncio
async def test_new_row_gets_categorized_and_commits():
    db = _db()
    await _orchestrator()._update_service_record(db, _NODE, "redis-server.service", "running")
    row = db.add.call_args.args[0]
    assert row.category == categorize_service("redis-server.service")
    assert (row.node_id, row.service_name, row.status) == (_NODE, "redis-server.service", "running")
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_row_is_updated_not_added():
    existing = MagicMock()
    db = _db(existing)
    await _orchestrator()._update_service_record(db, _NODE, "redis-server.service", "stopped")
    assert existing.status == "stopped"
    assert existing.last_checked is not None
    db.add.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_refused_name_warns_naming_service_and_node(caplog):
    db = _db()
    bad = "bad name; rm -rf /"
    with caplog.at_level(logging.WARNING, logger="services.service_orchestrator"):
        await _orchestrator()._update_service_record(db, _NODE, bad, "running")
    msgs = [r.getMessage() for r in caplog.records if r.name == "services.service_orchestrator"]
    assert any(bad in m and _NODE in m for m in msgs)
    db.add.assert_not_called()
