# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""The embedding switch cuts over only after a complete, zero-failure build (#19379)."""

from __future__ import annotations

import types

import pytest

from autobot_shared.embedding_model import EmbeddingSpace
from knowledge import embedding_switch as sw

pytestmark = pytest.mark.asyncio

MODEL, COLLECTION = "bge-m3:latest", "autobot_memory__bge_m3_latest"


class _Redis:
    def __init__(self):
        self.hashes, self.sets = {}, {}

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    async def delete(self, *keys):
        for k in keys:
            self.hashes.pop(k, None)
            self.sets.pop(k, None)

    async def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    async def spop(self, key):
        members = self.sets.get(key)
        return members.pop() if members else None


class _Collection:
    def __init__(self):
        self.ids: dict = {}

    def count(self):
        return len(self.ids)

    def delete(self, ids):
        for i in ids:
            self.ids.pop(i, None)


def _vector_store():
    """A LlamaIndex ChromaVectorStore stand-in: ``add(nodes)`` and the raw ``_collection``."""
    collection = _Collection()

    def add(nodes):
        for n in nodes:
            collection.ids[n.id_] = n

    return types.SimpleNamespace(add=add, _collection=collection)


@pytest.fixture
def env(monkeypatch):
    redis, store, log = _Redis(), _vector_store(), []
    facts = {f"f{i}": {"content": f"fact {i}", "metadata": {"k": i}} for i in range(5)}
    fake_fs = types.SimpleNamespace(
        count_facts=_async(lambda *a: len(facts)),
        load_fact=_async(lambda fid: facts.get(fid)),
        iter_facts=lambda batch_size: _batches(facts, batch_size, redis),
    )

    async def _open(kb, collection, model, dim):
        log.append(("open", collection, model, dim))
        return store

    async def _rebind(kb, space):
        log.append(("rebind", space.collection, space.generation))

    def _override(model, collection):
        log.append(("override", model, collection, store._collection.count()))
        return EmbeddingSpace(model, collection, 7)

    monkeypatch.setattr(sw, "_redis", _async(lambda: redis))
    monkeypatch.setattr(sw, "_open_collection", _open)
    monkeypatch.setattr(sw, "set_embedding_model_override", _override)
    monkeypatch.setattr(sw, "SWITCH_BATCH_SIZE", 2)
    monkeypatch.setattr(sw, "_settle_seconds", lambda: 0.0)
    monkeypatch.setattr(sw, "active_embedding_model", lambda: "nomic-embed-text:latest")
    import knowledge
    import knowledge.embedding_space_binding as binding
    import services.npu_client as npu

    monkeypatch.setattr(knowledge, "fact_store", fake_fs, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "knowledge.fact_store", fake_fs)
    monkeypatch.setattr(binding, "rebind", _rebind)
    env = types.SimpleNamespace(redis=redis, store=store, log=log, facts=facts, fail=set())

    async def _embed(texts, model_name=None):
        assert model_name == MODEL, "the rebuild embeds with the TARGET model, never the active one"
        return [None if t in env.fail else [0.1, 0.2] for t in texts]

    monkeypatch.setattr(npu, "generate_embeddings_batch_with_fallback", _embed)
    return env


def _async(fn):
    async def wrapper(*a, **kw):
        return fn(*a, **kw)

    return wrapper


async def _batches(facts, size, redis):
    ids = list(facts)
    for i in range(0, len(ids), size):
        yield [{"fact_id": f, **facts[f]} for f in ids[i : i + size]]


async def _start(env, kb=None):
    kb = kb or types.SimpleNamespace(chromadb_collection="autobot_memory", chromadb_path="/tmp")
    await sw._set_state(state="building", model=MODEL, collection=COLLECTION, done=0, total=0, failed=0)
    await sw.run_switch(kb, MODEL, COLLECTION)
    return await sw.switch_status()


async def test_a_complete_build_cuts_over_after_every_fact_is_in(env):
    status = await _start(env)
    assert status["state"] == "completed" and status["done"] == 5
    overrides = [e for e in env.log if e[0] == "override"]
    assert overrides == [("override", MODEL, COLLECTION, 5)], "cutover only once all 5 vectors are held"
    assert ("rebind", COLLECTION, 7) in env.log


async def test_the_active_space_is_untouched_until_the_build_completes(env):
    """Search keeps the old collection during the build: nothing switches before the last batch."""
    seen = []
    original = env.store.add

    def _add(nodes):
        seen.append(any(e[0] == "override" for e in env.log))
        original(nodes)

    env.store.add = _add
    await _start(env)
    assert seen and not any(seen), "the override was written while vectors were still being added"


async def test_one_failed_fact_blocks_the_cutover(env):
    env.fail.add("fact 3")
    status = await _start(env)
    assert status["state"] == "failed" and status["failed"] == 1 and status["failed_ids"] == ["f3"]
    assert not [e for e in env.log if e[0] in ("override", "rebind")]


async def test_a_cancel_stops_before_cutover(env):
    await sw._set_state(cancel="1")
    status = await _start(env)
    assert status["state"] == "cancelled"
    assert not [e for e in env.log if e[0] == "override"]


async def test_a_vector_count_that_does_not_match_the_facts_blocks_the_cutover(env, monkeypatch):
    env.facts["late"] = {"content": "never embedded", "metadata": {}}  # counted, but the walk misses it
    monkeypatch.setattr(sw, "note_write", _async(lambda fid: None))
    walk = dict(env.facts)
    walk.pop("late")
    import sys

    sys.modules["knowledge.fact_store"].iter_facts = lambda batch_size: _batches(walk, batch_size, env.redis)
    status = await _start(env)
    assert status["state"] == "failed" and "6 facts" in status["reason"]
    assert not [e for e in env.log if e[0] == "override"]


async def test_writes_during_the_build_are_replayed_and_deletes_removed(env):
    await sw._set_state(state="building")
    env.facts["new"] = {"content": "written during the build", "metadata": {}}
    await sw.note_write("new")
    env.facts.pop("f0")
    await sw.note_write("f0")  # deleted during the build
    status = await _start(env)
    assert status["state"] == "completed"
    assert "new" in env.store._collection.ids and "f0" not in env.store._collection.ids


async def test_a_write_is_not_noted_when_no_switch_runs(env):
    await sw.note_write("f1")
    assert env.redis.sets == {}


@pytest.mark.parametrize(
    "model, state, message",
    [
        ("phi3:mini", "idle", "not an embedding model"),
        (MODEL, "building", "already running"),
        ("nomic-embed-text:latest", "idle", "already the active"),
    ],
)
async def test_start_refuses(env, model, state, message):
    await sw._set_state(state=state)
    kb = types.SimpleNamespace(chromadb_collection="autobot_memory", chromadb_path="/tmp")
    with pytest.raises(sw.SwitchRefused, match=message):
        await sw.start_switch(kb, model)


def test_the_target_collection_name_fits_chromadb():
    name = sw.target_collection("autobot_memory", "BAAI/bge-small-en-v1.5:latest")
    assert name == "autobot_memory__baai_bge_small_en_v1_5_latest"
    assert 3 <= len(name) <= 63 and name[0].isalnum() and name[-1].isalnum()
