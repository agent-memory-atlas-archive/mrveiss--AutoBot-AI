# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The MCP knowledge-search tool never hands an LLM a rejected fact (#18820).

A caller-supplied filter replaces the shared grounding filter on this route
(#13009), so the store-side trust clause is absent there; the serving predicate
is applied to the results instead.
"""

from __future__ import annotations

from unittest.mock import create_autospec, patch

import pytest

from api.knowledge_mcp import KnowledgeSearchRequest, mcp_search_knowledge_base
from knowledge_base import KnowledgeBase

_HITS = [
    {"content": "kept", "score": 0.9, "metadata": {"verification_status": "verified"}},
    {"content": "rejected", "score": 0.9, "metadata": {"verification_status": "rejected"}},
    {"content": "garbage", "score": 0.9, "metadata": {"valid_to": "garbage"}},
    {"content": "pending", "score": 0.9, "metadata": {"verification_status": "pending_review"}},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("filters", [{"category": "docs"}, None], ids=["caller-filter", "default-filter"])
async def test_mcp_search_withholds_rejected_and_unparseable_facts(filters):
    mock_kb = create_autospec(KnowledgeBase, instance=True)
    mock_kb.search.return_value = list(_HITS)

    with patch("api.knowledge_mcp.get_knowledge_base", return_value=mock_kb):
        response = await mcp_search_knowledge_base(
            request=KnowledgeSearchRequest(query="redis", top_k=5, filters=filters),
            current_user={"user_id": "test-user"},
        )

    # pending_review is served until #18077 decides otherwise.
    assert [r["content"] for r in response["results"]] == ["kept", "pending"]
    assert response["count"] == 2
