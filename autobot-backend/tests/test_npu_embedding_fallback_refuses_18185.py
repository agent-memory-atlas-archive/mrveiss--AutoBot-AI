# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""#18185: the engine emits a query vector in the index's space or refuses; never another space."""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from ai_hardware_accelerator import HardwareDevice
from autobot_shared.embedding_model import EmbeddingUnavailableError

ACC = "npu_semantic_search.accelerated_embedding_generation"


def _instance():
    from npu_semantic_search import NPUSemanticSearch

    obj = NPUSemanticSearch.__new__(NPUSemanticSearch)
    obj._l1_hits = obj._l2_hits = obj._cache_misses = 0
    obj.embedding_cache = MagicMock(get=AsyncMock(return_value=None), put=AsyncMock())
    obj._l2_cache_get = AsyncMock(return_value=None)
    obj._l2_cache_set = AsyncMock()
    return obj


def _no_chunker():
    return patch("utils.semantic_chunker.get_semantic_chunker", side_effect=AssertionError("chunker used"))


@pytest.mark.asyncio
async def test_accelerated_paths_return_the_index_space_vector_with_correct_arguments():
    obj = _instance()
    acc = AsyncMock(return_value=np.array([0.1]))
    with patch(ACC, acc):
        _, label = await obj._generate_embedding_with_device("q", True, None)
        assert label == "auto_selected"
        acc.assert_awaited_with("q")
        _, label = await obj._generate_embedding_with_device("q", False, HardwareDevice.GPU)
        assert label == HardwareDevice.GPU.value
        acc.assert_awaited_with("q", preferred_device=HardwareDevice.GPU)


@pytest.mark.asyncio
@pytest.mark.parametrize("enable_npu,force", [(True, None), (False, HardwareDevice.CPU)])
async def test_accelerator_failure_refuses_and_caches_nothing(enable_npu, force, caplog):
    obj = _instance()
    with patch(ACC, AsyncMock(side_effect=RuntimeError("secret-detail"))), _no_chunker():
        with pytest.raises(EmbeddingUnavailableError, match="#18217") as exc:
            await obj._generate_optimized_embedding("q", enable_npu, force)
    assert "secret-detail" not in str(exc.value) and "secret-detail" not in caplog.text
    obj.embedding_cache.put.assert_not_awaited()
    obj._l2_cache_set.assert_not_awaited()


@pytest.mark.asyncio
async def test_npu_disabled_without_forced_device_refuses_visibly():
    """Behaviour change: enable_npu=False used to embed with the chunker; it now refuses."""
    obj = _instance()
    acc = AsyncMock()
    with patch(ACC, acc), _no_chunker():
        with pytest.raises(EmbeddingUnavailableError, match="disabled"):
            await obj._generate_optimized_embedding("q", False, None)
    acc.assert_not_awaited()
    obj.embedding_cache.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_surfaces_failed_search_with_reason_not_empty_results():
    obj = _instance()
    obj._generate_optimized_embedding = AsyncMock(side_effect=EmbeddingUnavailableError("model 'm' unavailable"))
    obj._get_cached_result = MagicMock(return_value=None)
    obj._generate_cache_key = MagicMock(return_value="k")
    obj.knowledge_base = MagicMock(search=AsyncMock(side_effect=AssertionError("text fallback used")))
    results, metrics = await obj.search("q")
    assert results == [] and metrics.error and "unavailable" in metrics.error
    assert metrics.device_used == "embedding_unavailable"


@pytest.mark.asyncio
async def test_batch_embedding_refusal_keeps_its_reason_per_query():
    obj = _instance()
    obj._get_cached_result = MagicMock(return_value=None)
    obj._generate_cache_key = MagicMock(return_value="k")
    obj.collections = {}
    obj._generate_optimized_embedding = AsyncMock(side_effect=EmbeddingUnavailableError("model 'm' unavailable"))
    ((results, metrics),) = await obj.batch_search(["q"])
    assert results == [] and metrics.error == "model 'm' unavailable" and metrics.device_used == "error"


def test_contrast_no_other_space_fallback_remains_in_the_engine():
    import inspect

    import npu_semantic_search as mod

    src = inspect.getsource(mod)
    assert "semantic_chunker" not in src and "embed_sentences" not in src
    assert "generate_embedding_with_fallback" not in src and not hasattr(
        mod.NPUSemanticSearch, "_generate_fallback_embedding"
    )
