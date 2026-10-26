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

from api import ai_stack_integration
from knowledge.read_path_fakes_16665 import PRIVATE_A, RESEARCH, SHARED, FakeKB, QuarantineGatedKB

ALICE = {"user_id": "alice", "role": "user"}
BOB = {"user_id": "bob", "role": "user"}
ADMIN = {"user_id": "root", "role": "admin"}


async def _search(user, kb) -> str:
    """The handler's own result, called directly: the route's response_model rejects its payload.

    ``KnowledgeSearchData`` requires ``rag_augmented`` while the handler emits ``aistack``, so a
    TestClient call fails response validation whatever the caller may read -- a contract defect
    separate from #16665, so this test reads the handler's return value.
    """
    client = AsyncMock()
    client.search_knowledge.return_value = {}
    with (
        patch.object(ai_stack_integration, "get_ai_stack_client", AsyncMock(return_value=client)),
        patch("services.audit_logger.audit_log", new=AsyncMock(return_value=True)),
    ):
        response = await ai_stack_integration.knowledge_search(
            query="q", admin_check=True, knowledge_base=kb, current_user=user
        )
    return repr(response)


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
