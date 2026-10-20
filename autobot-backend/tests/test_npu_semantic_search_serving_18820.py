# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""#18820/#18829: the NPU engine's retrieval paths that bypass KB.search apply the one serving predicate."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from knowledge.index_embedding_space import CODE_INDEX
from npu_semantic_search import NPUSemanticSearch

PAST = "2000-01-01T00:00:00+00:00"
FUTURE = "2999-01-01T00:00:00+00:00"
VALID = {"verification_status": "verified", "valid_to": FUTURE}
REJECTED = {"verification_status": "rejected"}
EXPIRED = {"valid_to": PAST}
CASES = {"valid": VALID, "rejected": REJECTED, "expired": EXPIRED}


def _engine() -> NPUSemanticSearch:
    obj = NPUSemanticSearch.__new__(NPUSemanticSearch)
    obj.collections, obj.collection_names = {}, {"text": "autobot_text_embeddings", "code": CODE_INDEX}
    return obj


def test_llamaindex_path_drops_rejected_and_expired_keeps_valid():
    obj = _engine()
    nodes = [
        SimpleNamespace(node=SimpleNamespace(text=name, metadata=meta, id_=name), score=0.5)
        for name, meta in CASES.items()
    ]
    engine = MagicMock()
    engine.query.return_value = SimpleNamespace(source_nodes=nodes)
    obj.knowledge_base = SimpleNamespace(vector_index=MagicMock(as_query_engine=MagicMock(return_value=engine)))
    out = asyncio.run(obj._search_with_llamaindex("q", 5))
    assert [r.doc_id for r in out] == ["valid"]


def test_hybrid_path_drops_rejected_and_expired_keeps_valid(monkeypatch):
    import npu_semantic_search as mod

    obj = _engine()
    obj._generate_optimized_embedding = AsyncMock(return_value=([0.0], "cpu"))
    monkeypatch.setattr(mod, "require_query_dim", lambda *_: None)
    hits = [SimpleNamespace(content=n, metadata=m, score=0.5, doc_id=n) for n, m in CASES.items()]
    metrics = SimpleNamespace(gpu_utilized=False, query_time_ms=1.0, backend_used=SimpleNamespace(value="faiss"))
    obj.hybrid_search = SimpleNamespace(search=AsyncMock(return_value=(hits, metrics)))
    out, _ = asyncio.run(obj._search_hybrid_index("q", 5, None))
    assert [r.doc_id for r in out] == ["valid"]


def test_batch_chromadb_path_drops_rejected_and_expired_keeps_valid():
    obj = _engine()
    ids = list(CASES)
    batch = {
        "ids": [ids],
        "documents": [ids],
        "metadatas": [[CASES[i] for i in ids]],
        "distances": [[0.1, 0.2, 0.3]],
    }
    obj.collections["text"] = SimpleNamespace(query_batch=AsyncMock(return_value=batch))
    out = asyncio.run(obj._batch_chromadb_search([0], [[0.0]], 5, None, "cpu"))
    results, _ = out[0]
    assert [r.doc_id for r in results] == ["valid"]


def test_a_result_with_no_status_is_a_legacy_fact_and_is_served():
    obj = _engine()
    batch = {"ids": [["old"]], "documents": [["x"]], "metadatas": [[{}]], "distances": [[0.1]]}
    obj.collections["text"] = SimpleNamespace(query_batch=AsyncMock(return_value=batch))
    results, _ = asyncio.run(obj._batch_chromadb_search([0], [[0.0]], 5, None, "cpu"))[0]
    assert [r.doc_id for r in results] == ["old"]


def test_batch_path_reads_the_text_collection_not_the_code_collection():
    """Pins which collection the batch path serves from: KB text, so the predicate applies."""
    obj = _engine()
    assert obj.collection_names["text"] != obj.collection_names["code"]
    obj.collections["code"] = SimpleNamespace(query_batch=AsyncMock(side_effect=AssertionError("code read")))
    obj.collections["text"] = SimpleNamespace(
        query_batch=AsyncMock(return_value={"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]})
    )
    assert asyncio.run(obj._batch_chromadb_search([0], [[0.0]], 5, None, "cpu"))[0][0] == []
