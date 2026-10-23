# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The backend's Redis DB readers follow the one table (#19232).

These read ``ssot_config.RedisConfig.db_main`` / ``db_knowledge`` / ``db_tasks`` before
#19232 -- a parallel, env-backed table the canonical client never consulted. Each one is
shown moving when the one table moves (``AUTOBOT_REDIS_DB_MAIN`` / ``_KNOWLEDGE`` /
``_TASKS``), so none of them can be left behind on a second copy.
"""

import pytest

import config as config_pkg
from autobot_shared.redis_management import types as _types
from config import defaults

_OVERRIDES = {"AUTOBOT_REDIS_DB_MAIN": "12", "AUTOBOT_REDIS_DB_KNOWLEDGE": "0", "AUTOBOT_REDIS_DB_TASKS": "1"}


@pytest.fixture
def overridden(monkeypatch):
    """Rebuild THE table in place from the YAML plus ``_OVERRIDES``, as process start would."""
    monkeypatch.setattr(_types, "override_source", lambda: dict(_OVERRIDES))
    rebuilt = _types._build_database_mapping()
    saved = dict(_types.DATABASE_MAPPING)
    _types.DATABASE_MAPPING.clear()
    _types.DATABASE_MAPPING.update(rebuilt)
    yield
    _types.DATABASE_MAPPING.clear()
    _types.DATABASE_MAPPING.update(saved)


def test_the_canonical_redis_config_follows_main(overridden):
    assert config_pkg.unified_config_manager.get_redis_config()["db"] == 12


def test_the_reentry_stub_follows_main(overridden):
    assert config_pkg._ConfigStub().get_redis_config()["db"] == 12


def test_the_defaults_follow_main_knowledge_and_workflows(overridden):
    assert defaults._get_redis_config("h", 1)["db"] == 12
    assert defaults._get_memory_config("h", 1)["redis"]["db"] == 0
    assert defaults._get_task_transport_config("h", 1)["redis"]["db"] == 1


def test_the_cache_api_reports_the_moved_numbers(overridden):
    from api.cache_management import redis_databases

    databases = redis_databases()
    assert (databases["main"], databases["knowledge"]) == (12, 0)
    assert databases["websockets"] is None, "a name nothing declares has no number"
