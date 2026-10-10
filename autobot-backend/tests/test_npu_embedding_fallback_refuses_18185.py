# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""#18185: the embedding fallback uses the canonical model or refuses, never another model."""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from autobot_shared.embedding_model import EmbeddingUnavailableError

ACCESSOR = "npu_semantic_search.generate_embedding_with_fallback"


def _instance():
    from npu_semantic_search import NPUSemanticSearch

    obj = NPUSemanticSearch.__new__(NPUSemanticSearch)
    obj._l1_hits = obj._l2_hits = obj._cache_misses = 0
    obj.embedding_cache = MagicMock(get=AsyncMock(return_value=None), put=AsyncMock())
    obj._l2_cache_get = AsyncMock(return_value=None)
    obj._l2_cache_set = AsyncMock()
    return obj


def _no_chunker():
    """The chunker must never be asked for a vector."""
    return patch("utils.semantic_chunker.get_semantic_chunker", side_effect=AssertionError("chunker used"))


@pytest.mark.asyncio
async def test_fallback_uses_canonical_accessor_with_active_model():
    obj = _instance()
    with patch(ACCESSOR, AsyncMock(return_value=[0.1, 0.2])) as acc, _no_chunker():
        vec, label = await obj._generate_fallback_embedding("q")
    assert np.allclose(vec, [0.1, 0.2]) and label == "cpu_final_fallback"
    acc.assert_awaited_once()


@pytest.mark.asyncio
async def test_gpu_fallback_path_uses_canonical_accessor():
    obj = _instance()
    with patch(ACCESSOR, AsyncMock(return_value=[0.3])), _no_chunker():
        vec, label = await obj._generate_embedding_with_device("q", False, None)
    assert np.allclose(vec, [0.3]) and label == "gpu_fallback"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [{"return_value": None}, {"side_effect": RuntimeError("down")}])
async def test_unavailable_refuses_and_leaves_cache_untouched(failure, caplog):
    obj = _instance()
    with patch(ACCESSOR, AsyncMock(**failure)), _no_chunker():
        with patch.object(obj, "_generate_embedding_with_device", AsyncMock(side_effect=RuntimeError("primary"))):
            with pytest.raises(EmbeddingUnavailableError):
                await obj._generate_optimized_embedding("q", False, None)
    obj.embedding_cache.put.assert_not_awaited()
    obj._l2_cache_set.assert_not_awaited()
    assert "Embedding fallback refused" in caplog.text


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


def test_contrast_old_fallback_would_have_used_the_chunker():
    """The old body called the chunker's embed hook; the new source must not."""
    import inspect

    import npu_semantic_search as mod

    src = inspect.getsource(mod.NPUSemanticSearch._generate_fallback_embedding)
    assert "semantic_chunker" not in src and "embed_sentences" not in src


@pytest.mark.asyncio
async def test_accelerated_paths_stay_in_the_index_space_with_correct_arguments():
    """The query vector must match the indexes (accelerator space); force_device is a device, not a modality."""
    from ai_hardware_accelerator import HardwareDevice

    obj = _instance()
    acc = AsyncMock(return_value=np.array([0.1]))
    with patch("npu_semantic_search.accelerated_embedding_generation", acc):
        _, label = await obj._generate_embedding_with_device("q", True, None)
        assert label == "auto_selected"
        acc.assert_awaited_with("q")
        _, label = await obj._generate_embedding_with_device("q", True, HardwareDevice.GPU)
        assert label == HardwareDevice.GPU.value
        acc.assert_awaited_with("q", preferred_device=HardwareDevice.GPU)


@pytest.mark.asyncio
async def test_batch_embedding_refusal_keeps_its_reason_per_query():
    """F5: a refused batch query is an explicit error result carrying the reason, not a bare empty one."""
    obj = _instance()
    obj._get_cached_result = MagicMock(return_value=None)
    obj._generate_cache_key = MagicMock(return_value="k")
    obj.collections = {}
    obj._generate_optimized_embedding = AsyncMock(side_effect=EmbeddingUnavailableError("model 'm' unavailable"))
    ((results, metrics),) = await obj.batch_search(["q"])
    assert results == [] and metrics.error == "model 'm' unavailable" and metrics.device_used == "error"
