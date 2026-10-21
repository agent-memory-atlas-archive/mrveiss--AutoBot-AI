# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A caller-supplied MCP search filter narrows the quarantine filter, never replaces it (#18946)."""

from __future__ import annotations

from unittest.mock import create_autospec, patch

import chromadb
import pytest

from api.knowledge_mcp import KnowledgeSearchRequest, mcp_search_knowledge_base
from autobot_shared.ssot_config import config
from knowledge.quarantine import RESEARCH_QUARANTINE_FILTER, with_quarantine
from knowledge_base import KnowledgeBase


def _collection():
    c = chromadb.EphemeralClient().get_or_create_collection(f"t18946_{id(object())}")
    quarantine = config.research_quarantine_collection
    c.add(
        ids=["ok", "bad"],
        documents=["a", "b"],
        metadatas=[
            {"category": "docs", "collection": "main"},
            {"category": "docs", "collection": quarantine},
        ],
    )
    return c


def _ids(c, where):
    return set(c.get(where=where)["ids"])


def test_caller_filter_does_not_leak_quarantined_fact():
    c = _collection()
    assert _ids(c, with_quarantine({"category": "docs"})) == {"ok"}


def test_caller_filter_still_narrows():
    c = _collection()
    assert _ids(c, with_quarantine({"category": "other"})) == set()


def test_no_caller_filter_is_bare_quarantine():
    assert with_quarantine(None) is RESEARCH_QUARANTINE_FILTER
    assert with_quarantine({}) is RESEARCH_QUARANTINE_FILTER


@pytest.mark.asyncio
async def test_mcp_route_ands_caller_filter_with_quarantine():
    mock_kb = create_autospec(KnowledgeBase, instance=True)
    mock_kb.search.return_value = []
    with patch("api.knowledge_mcp.get_knowledge_base", return_value=mock_kb):
        await mcp_search_knowledge_base(
            request=KnowledgeSearchRequest(query="q", top_k=5, filters={"category": "docs"}),
            current_user={"user_id": "u"},
        )
    assert mock_kb.search.call_args.kwargs["filters"] == {"$and": [RESEARCH_QUARANTINE_FILTER, {"category": "docs"}]}
