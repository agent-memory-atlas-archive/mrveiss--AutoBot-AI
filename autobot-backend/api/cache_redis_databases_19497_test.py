# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The cache API's settings persist, and a clear never flushes a database it did not name (#19497).

The settings were written to ``config``, a database nothing declares, so every save reported
success and stored nothing. ``locks`` and ``conversations`` are aliases, so clearing ``locks``
flushed the sessions DB and ``clear/all`` flushed sessions and agents twice each.
"""

import asyncio
from collections import Counter

import pytest
from fastapi import HTTPException

from api import cache_management, cache_redis_databases
from autobot_shared.redis_management import types as _types
from autobot_shared.redis_management.types import DATABASE_MAPPING

_CONFIG = {"defaultTTLMinutes": 7, "settingsTTLMinutes": 11, "autoCleanupEnabled": False, "maxCacheSizeMB": 64}


class _FakeRedis:
    """One Redis database: a dict, and a log of the flushes it received."""

    def __init__(self, number: int, flushes: Counter):
        self.number, self.flushes, self.data = number, flushes, {}

    def set(self, key, value):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)

    def dbsize(self):
        return len(self.data)

    def flushdb(self):
        self.flushes[self.number] += 1
        self.data.clear()


@pytest.fixture
def redis(monkeypatch):
    """A fake server: one database per NUMBER, so an alias reaches its target's database."""
    flushes: Counter = Counter()
    databases: dict = {}

    def get_redis_client(async_client, database):
        number = DATABASE_MAPPING[database]  # refuses an undeclared name, as the real client does
        return databases.setdefault(number, _FakeRedis(number, flushes))

    monkeypatch.setattr(cache_redis_databases, "get_redis_client", get_redis_client)
    return databases, flushes


def test_a_saved_config_loads_back_from_the_declared_cache_db(redis):
    databases, _ = redis
    asyncio.run(cache_management.save_cache_config(dict(_CONFIG)))
    loaded = asyncio.run(cache_management.get_cache_config())
    assert (loaded["config"], loaded["source"]) == (_CONFIG, "redis")
    stored = databases[DATABASE_MAPPING[cache_redis_databases.CACHE_CONFIG_DATABASE]]
    assert list(stored.data) == ["cache:config"], "one namespaced key in the cache DB, nothing else"


def test_a_failed_save_is_an_error_not_a_success(monkeypatch):
    def unavailable(async_client, database):
        raise ConnectionError("redis down")

    monkeypatch.setattr(cache_redis_databases, "get_redis_client", unavailable)
    with pytest.raises(HTTPException) as raised:
        asyncio.run(cache_management.save_cache_config(dict(_CONFIG)))
    assert raised.value.status_code == 503


def test_the_cache_config_is_stored_in_a_declared_database():
    assert cache_redis_databases.CACHE_CONFIG_DATABASE in _types.DECLARED_DATABASES


def test_no_alias_is_offered_for_clearing_or_stats():
    offered = set(cache_redis_databases.redis_databases())
    assert offered and offered <= set(_types.DECLARED_DATABASES)
    assert not offered & set(_types._ALIASES)


@pytest.mark.parametrize("alias", sorted(_types._ALIASES))
def test_clearing_an_alias_is_a_400_naming_its_target_and_flushes_nothing(redis, alias):
    _, flushes = redis
    with pytest.raises(HTTPException) as raised:
        asyncio.run(cache_management.clear_redis_cache(alias))
    assert raised.value.status_code == 400
    assert f"alias of '{_types._ALIASES[alias]}'" in raised.value.detail
    assert not flushes


def test_clearing_one_database_flushes_exactly_that_one(redis):
    _, flushes = redis
    asyncio.run(cache_management.clear_redis_cache("sessions"))
    assert flushes == Counter({DATABASE_MAPPING["sessions"]: 1})


def test_clear_all_flushes_each_distinct_database_exactly_once(redis):
    _, flushes = redis
    result = asyncio.run(cache_management.clear_redis_cache("all"))
    expected = set(cache_redis_databases.redis_databases().values())
    assert flushes == Counter({number: 1 for number in expected})
    assert len(result["cleared_databases"]) == len(expected)


def test_clear_all_flushes_a_shared_number_once(redis, monkeypatch):
    """Two names an override puts on one number are one database: flushed once, reported once."""
    _, flushes = redis
    monkeypatch.setitem(DATABASE_MAPPING, "analytics", DATABASE_MAPPING["cache"])
    asyncio.run(cache_management.clear_redis_cache("all"))
    assert max(flushes.values()) == 1
    assert flushes[DATABASE_MAPPING["cache"]] == 1
