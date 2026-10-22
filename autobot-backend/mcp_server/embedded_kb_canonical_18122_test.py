# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The embedded MCP client resolves the canonical, initialized KB (#18122 D2).

It used to build ``KnowledgeBase(config_manager=config)`` and never await
``initialize()``, handing MCP callers an instance with ``initialized is False``.
"""

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_EMBEDDED = (
    Path(__file__).resolve().parents[2]
    / "autobot-infrastructure/shared/mcp/tools/knowledge-base-mcp/autobot_knowledge_mcp/embedded.py"
)


def _client():
    spec = importlib.util.spec_from_file_location("autobot_knowledge_mcp_embedded_18122", _EMBEDDED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.EmbeddedKnowledgeClient()


@pytest.mark.asyncio
async def test_the_embedded_client_returns_the_canonical_initialized_kb():
    kb = MagicMock(initialized=True)
    with patch("knowledge_factory.get_knowledge_base_async", AsyncMock(return_value=kb)) as accessor:
        client = _client()
        assert await client._ensure_initialized() is kb
        assert await client._ensure_initialized() is kb
    accessor.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_none_accessor_raises_instead_of_a_private_fallback():
    with patch("knowledge_factory.get_knowledge_base_async", AsyncMock(return_value=None)):
        client = _client()
        with pytest.raises(RuntimeError, match="Knowledge base unavailable"):
            await client._ensure_initialized()
    assert client._kb is None
