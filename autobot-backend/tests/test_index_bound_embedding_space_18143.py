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


def _searcher(metadata):
    from services.knowledge.doc_searcher import DocumentationSearcher

    searcher = DocumentationSearcher()
    searcher._collection = MagicMock(metadata=metadata)
    return searcher


def test_the_doc_searcher_takes_the_space_the_collection_records(active_changed) -> None:
    from autobot_shared.embedding_provenance import EmbeddingProvenance, provenance_to_metadata

    space = _searcher(provenance_to_metadata(EmbeddingProvenance("mxbai-embed-large", 1024)))._recorded_space()
    assert (space.model_name, space.dim) == ("mxbai-embed-large", 1024)


def _unrecorded(stored_dim: int | None, probe_dim: int):
    """A searcher whose collection records no space, stores ``stored_dim``-d vectors, active model gives probe_dim."""
    import sys
    import types

    from services.knowledge import doc_searcher

    searcher = _searcher({"hnsw:space": "cosine"})
    embs = None if stored_dim is None else [[0.0] * stored_dim]
    searcher._collection.peek.return_value = {"embeddings": embs}
    fake = types.ModuleType("llama_index.embeddings.ollama")
    fake.OllamaEmbedding = lambda **kw: MagicMock(get_text_embedding=lambda t: [0.0] * probe_dim)
    doc_searcher._WARNED.clear()
    return searcher, patch.dict(sys.modules, {"llama_index.embeddings.ollama": fake})


def test_an_unrecorded_collection_with_a_matching_dimension_is_searched_warns_and_proposes(caplog) -> None:
    searcher, modules = _unrecorded(768, 768)
    with modules, patch("services.knowledge.collection_space_proposal.schedule_stamp_proposal") as propose:
        space = searcher._recorded_space()
        searcher._recorded_space()  # a second init in the same process does not warn or propose again
    assert space.dim == 768 and space.model_name
    propose.assert_called_once_with("autobot_docs", space.model_name, 768)
    assert caplog.text.count("unrecorded space, dimension-matched") == 1


def test_an_unrecorded_collection_with_a_different_dimension_or_empty_is_refused() -> None:
    for stored, probe in ((384, 768), (None, 768)):
        searcher, modules = _unrecorded(stored, probe)
        with modules, patch("services.knowledge.collection_space_proposal.schedule_stamp_proposal") as propose:
            assert searcher._recorded_space() is None
        propose.assert_not_called()


async def test_the_stamp_proposal_is_reused_when_one_is_pending_and_never_writes_the_collection() -> None:
    from services.knowledge import collection_space_proposal as csp

    existing = MagicMock(context={"collection": "autobot_docs"})
    session = MagicMock(execute=AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [existing]))))
    factory = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock()))
    with (
        patch.object(csp, "get_async_session_factory", return_value=factory),
        patch.object(csp, "ApprovalService") as service,
    ):
        assert await csp.propose_stamp("autobot_docs", "m", 768) is True
    service.return_value.request_approval.assert_not_called()


def test_the_cognition_seeder_refuses_a_collection_recorded_in_another_space() -> None:
    from autobot_shared.embedding_provenance import EmbeddingMismatchError, EmbeddingProvenance, provenance_to_metadata
    from services.knowledge.cognition_seeder import CognitionSeeder

    seeder = CognitionSeeder()
    seeder._model_name, seeder._embed_dim = "new-model", 512
    old = provenance_to_metadata(EmbeddingProvenance("nomic-embed-text", 768))
    seeder._client = MagicMock()
    seeder._client.get_or_create_collection.return_value = MagicMock(metadata=old)
    with pytest.raises(EmbeddingMismatchError):
        seeder._get_or_create_collection("cognition_store")
    new = provenance_to_metadata(EmbeddingProvenance("new-model", 512))
    seeder._client.get_or_create_collection.return_value = MagicMock(metadata=new)
    assert seeder._get_or_create_collection("cognition_store") is not None
    assert seeder._client.get_or_create_collection.call_args.kwargs["metadata"]["hnsw:space"] == "cosine"
