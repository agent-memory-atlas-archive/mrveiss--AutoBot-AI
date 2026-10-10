# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""#18185: in-process callers surface an embedding refusal instead of reporting no matches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from autobot_shared.embedding_model import EmbeddingUnavailableError, raise_if_embedding_refused

REFUSED = SimpleNamespace(
    error="embedding model 'm' unavailable: no vector returned", total_search_time_ms=1, device_used="x"
)
OK = SimpleNamespace(error=None, total_search_time_ms=1, device_used="x")


def test_helper_raises_only_on_refusal():
    raise_if_embedding_refused(OK)
    with pytest.raises(EmbeddingUnavailableError):
        raise_if_embedding_refused(REFUSED)


@pytest.mark.asyncio
async def test_vector_search_engine_npu_backend_raises_not_empty():
    from knowledge.vector_search_engine import _NPUBackend

    backend = _NPUBackend()
    backend._engine = MagicMock(search=AsyncMock(return_value=([], REFUSED)))
    with pytest.raises(EmbeddingUnavailableError):
        await backend.search("q", 5, None)
    backend._engine = MagicMock(search=AsyncMock(return_value=([], OK)))
    assert await backend.search("q", 5, None) == []  # a genuine no-match stays empty


@pytest.mark.asyncio
async def test_code_search_agent_raises_not_zero_results():
    from agents.npu_code_search_agent import NPUCodeSearchAgent

    agent = NPUCodeSearchAgent.__new__(NPUCodeSearchAgent)
    agent._ensure_search_engine_initialized = AsyncMock()
    agent.npu_available = True
    agent.npu_search_engine = MagicMock(search=AsyncMock(return_value=([], REFUSED)))
    with pytest.raises(EmbeddingUnavailableError):
        await agent._run_npu_semantic_search("q", None, 5)


@pytest.mark.asyncio
async def test_connectivity_test_reports_failed_not_success():
    from unittest.mock import patch

    from api import search as mod

    engine = MagicMock(
        npu_worker_url="u", _test_npu_connectivity=AsyncMock(), search=AsyncMock(return_value=([], REFUSED))
    )
    with patch.object(mod, "get_npu_search_engine", AsyncMock(return_value=engine)):
        out = await mod.test_npu_connectivity()
    assert out["connectivity"] == "failed"
