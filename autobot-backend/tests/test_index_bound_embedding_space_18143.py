# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Index-bound pipelines embed in their index's recorded space; fresh ones follow the active model (#18143)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.npu_client as npu_client_module
from autobot_shared.embedding_model import EmbeddingUnavailableError
from knowledge.index_embedding_space import PATTERN_INDEXES, index_embedding_space

ACTIVE = "some-other-model:latest"


@pytest.fixture
def active_changed():
    with patch("autobot_shared.embedding_model.active_embedding_model", return_value=ACTIVE):
        yield


@pytest.mark.parametrize("index", PATTERN_INDEXES)
def test_every_pattern_index_has_a_recorded_space(index: str) -> None:
    space = index_embedding_space(index)
    assert (space.model_id, space.dim, space.space_id) == ("nomic-embed-text", 768, "nomic-embed-text/768")


def test_an_unrecorded_collection_is_refused() -> None:
    with pytest.raises(EmbeddingUnavailableError):
        index_embedding_space("bug_pattern_vectors_test")


async def test_the_analytics_mixin_keys_and_generates_in_the_collection_space(active_changed) -> None:
    from code_intelligence.analytics_infrastructure import AnalyticsInfrastructureMixin

    class Probe(AnalyticsInfrastructureMixin):
        pass

    probe = Probe()
    probe._init_infrastructure(collection_name="security_analysis_vectors")
    cache = MagicMock(get=AsyncMock(return_value=None), put=AsyncMock())
    probe._get_embedding_cache = AsyncMock(return_value=cache)
    with patch.object(npu_client_module, "generate_embedding_with_fallback", new=AsyncMock(return_value=[0.1])) as gen:
        await probe._get_embedding("code")

    gen.assert_awaited_once_with("code", model_name="nomic-embed-text")
    cache.get.assert_awaited_once_with("code", model="nomic-embed-text/768")
    cache.put.assert_awaited_once_with("code", [0.1], model="nomic-embed-text/768")


async def test_the_analytics_mixin_refuses_a_collection_with_no_recorded_space() -> None:
    from code_intelligence.analytics_infrastructure import AnalyticsInfrastructureMixin

    class Probe(AnalyticsInfrastructureMixin):
        pass

    probe = Probe()
    probe._init_infrastructure(collection_name="unrecorded_vectors")
    probe._get_embedding_cache = AsyncMock(return_value=MagicMock(get=AsyncMock(return_value=None)))
    with pytest.raises(EmbeddingUnavailableError):
        await probe._get_embedding("code")


def test_fresh_embedders_follow_the_active_model(active_changed) -> None:
    from autobot_memory_graph.core import config as graph_config
    from autobot_shared.embedding_model import active_npu_worker_model

    assert active_npu_worker_model() == "some-other-model"  # the worker form drops the tag
    assert graph_config.embedding_model == ACTIVE
