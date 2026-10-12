# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""get_or_create_entity is atomic and never reads a Redis error as 'absent' (#18417)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from autobot_memory_graph import AutoBotMemoryGraph


class FakeRedis:
    """Just enough Redis: strings with NX, JSON documents, and an injectable outage."""

    def __init__(self):
        self.strings, self.docs, self.down = {}, {}, False

    async def set(self, key, value, nx=False):
        await asyncio.sleep(0)  # yield, so concurrent callers genuinely interleave
        self._check()
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    async def get(self, key):
        await asyncio.sleep(0)
        self._check()
        return self.strings.get(key)

    async def delete(self, key):
        self.strings.pop(key, None)

    def json(self):
        outer = self

        class _Json:
            async def get(self, key):
                await asyncio.sleep(0)
                outer._check()
                return outer.docs.get(key)

            async def set(self, key, path, doc):
                await asyncio.sleep(0)
                outer._check()
                outer.docs[key] = doc

        return _Json()

    def _check(self):
        if self.down:
            raise ConnectionError("redis down")


def _graph(redis, existing_by_name=None):
    graph = AutoBotMemoryGraph()
    graph._initialized = True
    graph.redis_client = redis
    graph.knowledge_base = None
    graph.get_entity = AsyncMock(return_value=existing_by_name)
    return graph


@pytest.mark.asyncio
async def test_concurrent_double_upsert_yields_one_entity():
    redis = FakeRedis()
    graph = _graph(redis)

    results = await asyncio.gather(
        graph.get_or_create_entity("TASK", "Redis", ["a"]),
        graph.get_or_create_entity("TASK", "Redis", ["a"]),
    )

    assert len(redis.docs) == 1
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0]["id"] == results[1][0]["id"]


@pytest.mark.asyncio
async def test_name_case_is_not_identity():
    redis = FakeRedis()
    graph = _graph(redis)
    await graph.get_or_create_entity("TASK", "Redis", [])
    _, created = await graph.get_or_create_entity("TASK", " redis ", [])
    assert created is False and len(redis.docs) == 1


@pytest.mark.asyncio
async def test_redis_error_raises_and_creates_nothing():
    redis = FakeRedis()
    redis.down = True
    graph = _graph(redis)

    with pytest.raises(ConnectionError):
        await graph.get_or_create_entity("TASK", "Redis", [])

    assert redis.docs == {} and redis.strings == {}


@pytest.mark.asyncio
async def test_legacy_entity_without_index_is_adopted_not_duplicated():
    redis = FakeRedis()
    legacy = {"id": "old-1", "name": "Redis", "_resolution": "exact"}
    redis.docs["memory:entity:old-1"] = legacy
    graph = _graph(redis, existing_by_name=legacy)

    entity, created = await graph.get_or_create_entity("TASK", "Redis", [])

    assert created is False and entity["id"] == "old-1"
    assert list(redis.docs) == ["memory:entity:old-1"]


@pytest.mark.asyncio
async def test_failed_write_releases_the_claim():
    redis = FakeRedis()
    graph = _graph(redis)
    graph._write_new_entity = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await graph.get_or_create_entity("TASK", "Redis", [])

    assert redis.strings == {}, "a failed create must not leave a claim that blocks retries"
