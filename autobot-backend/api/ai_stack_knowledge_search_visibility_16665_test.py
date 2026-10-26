# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""POST /knowledge/search returns local KB hits only for facts the caller may read (#16665).

An explicit, admin-gated read API (not prompt grounding): user A reads their private fact, user B
never does, an admin does, and a research-quarantined fact is never returned.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import ai_stack_integration
from auth_middleware import check_admin_permission, get_current_user
from dependencies import provide_knowledge_base
from knowledge.read_path_fakes_16665 import PRIVATE_A, RESEARCH, SHARED, FakeKB, QuarantineGatedKB

ALICE = {"user_id": "alice", "role": "user"}
BOB = {"user_id": "bob", "role": "user"}
ADMIN = {"user_id": "root", "role": "admin"}


def _search_http(user, kb):
    """Through the real router, so the route's response model is exercised too."""
    app = FastAPI()
    app.include_router(ai_stack_integration.router)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[check_admin_permission] = lambda: True
    app.dependency_overrides[provide_knowledge_base] = lambda: kb
    client = AsyncMock()
    client.search_knowledge.return_value = {}
    with (
        patch.object(ai_stack_integration, "get_ai_stack_client", AsyncMock(return_value=client)),
        patch("services.audit_logger.audit_log", new=AsyncMock(return_value=True)),
    ):
        return TestClient(app).post("/knowledge/search", params={"query": "q"})


async def _search(user, kb) -> str:
    return _search_http(user, kb).text


def test_the_response_matches_its_declared_model():
    response = _search_http(ALICE, FakeKB())

    assert response.status_code == 200
    assert set(response.json()["data"]) == {"local_kb", "rag_augmented"}


@pytest.mark.asyncio
async def test_user_a_reads_their_private_fact_and_user_b_never_does():
    for_alice, for_bob = await _search(ALICE, FakeKB()), await _search(BOB, FakeKB())

    assert PRIVATE_A in for_alice
    assert PRIVATE_A not in for_bob and SHARED in for_bob


@pytest.mark.asyncio
async def test_an_admin_reads_another_users_private_fact():
    assert PRIVATE_A in await _search(ADMIN, FakeKB())


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [ALICE, BOB, ADMIN])
async def test_a_research_quarantined_fact_is_never_returned(user):
    body = await _search(user, QuarantineGatedKB())

    assert RESEARCH not in body and SHARED in body
