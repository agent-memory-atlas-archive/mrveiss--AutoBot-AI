# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""A KB process follows a switch made anywhere, rebinding every collection-bound object (#19379)."""

from __future__ import annotations

import pytest

from autobot_shared.embedding_model import EmbeddingSpace
from knowledge import embedding_space_binding as binding

pytestmark = pytest.mark.asyncio


class _Buffer:
    def __init__(self, log, name):
        self.log, self.name = log, name

    async def stop(self):
        self.log.append(f"stop:{self.name}")

    async def start(self):
        self.log.append(f"start:{self.name}")


class _KB:
    def __init__(self, log):
        self.log = log
        self.chromadb_path = "/tmp/chroma"
        self.chromadb_collection = "autobot_memory"
        self._write_buffer = _Buffer(log, "autobot_memory")
        self.vector_index = object()
        self._bound_generation = 0
        self._space_checked_at = 0.0

    def _build_hnsw_metadata(self):
        return {"hnsw:space": "cosine"}

    async def _create_chroma_collection(self, client, meta):
        self.log.append(f"collection:{self.chromadb_collection}")
        self._write_buffer = _Buffer(self.log, self.chromadb_collection)

    def _configure_embedding_provider(self, provider, model, endpoint, cfg):
        self.log.append(f"embed:{model}")
        return 1024


@pytest.fixture
def kb(monkeypatch):
    monkeypatch.setattr(binding, "SPACE_CHECK_INTERVAL_S", 0.0)
    import knowledge.backends as backends

    monkeypatch.setattr(backends, "get_default_client", lambda **kw: object())
    return _KB([])


def _space(monkeypatch, space):
    monkeypatch.setattr(binding, "active_embedding_space", lambda: space)


async def test_a_newer_generation_rebinds_collection_buffer_index_and_model(monkeypatch, kb):
    _space(monkeypatch, EmbeddingSpace("bge-m3:latest", "kb_bge", 1))
    assert await binding.ensure_current_space(kb) is True
    # the old buffer drains into the OLD collection before anything else moves
    assert kb.log == ["stop:autobot_memory", "collection:kb_bge", "embed:bge-m3:latest", "start:kb_bge"]
    assert (kb.chromadb_collection, kb.vector_index, kb._bound_generation) == ("kb_bge", None, 1)
    assert (kb.embedding_model_name, kb.embedding_dimensions) == ("bge-m3:latest", 1024)


async def test_the_bound_generation_does_not_rebind_again(monkeypatch, kb):
    _space(monkeypatch, EmbeddingSpace("bge-m3:latest", "kb_bge", 1))
    await binding.ensure_current_space(kb)
    kb.log.clear()
    assert await binding.ensure_current_space(kb) is False
    assert kb.log == []


@pytest.mark.parametrize("space", [None, EmbeddingSpace("bge-m3:latest", None, 0)])
async def test_no_switch_or_a_legacy_record_leaves_the_kb_alone(monkeypatch, kb, space):
    _space(monkeypatch, space)
    assert await binding.ensure_current_space(kb) is False
    assert kb.log == [] and kb.chromadb_collection == "autobot_memory"


async def test_the_check_is_rate_limited(monkeypatch, kb):
    monkeypatch.setattr(binding, "SPACE_CHECK_INTERVAL_S", 60.0)
    kb._space_checked_at = binding.time.monotonic()
    _space(monkeypatch, EmbeddingSpace("bge-m3:latest", "kb_bge", 1))
    assert await binding.ensure_current_space(kb) is False


def test_init_binds_a_switched_collection(monkeypatch, kb):
    _space(monkeypatch, EmbeddingSpace("bge-m3:latest", "kb_bge", 4))
    assert binding.initial_collection(kb, "autobot_memory") == "kb_bge"
    assert kb._bound_generation == 4


def test_init_without_a_switch_keeps_the_configured_collection(monkeypatch, kb):
    _space(monkeypatch, None)
    assert binding.initial_collection(kb, "autobot_memory") == "autobot_memory"
    assert kb._bound_generation == 0
