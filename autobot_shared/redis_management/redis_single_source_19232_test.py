# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""One source for Redis database numbers and one for the endpoint (#19232).

Before: ``AUTOBOT_REDIS_DB_KNOWLEDGE=9`` moved ``ssot_config.RedisConfig.db_knowledge``
readers and left the canonical client on DB 1; ``get_redis_url_for_db`` read the SSOT
host while the canonical client could resolve another tier. Here the override feeds the
one table and every reader follows it, and a URL names the client's host/port/db/auth.
"""

from __future__ import annotations

from urllib.parse import unquote, urlparse

import pytest

from autobot_shared.redis_management import endpoint as _endpoint
from autobot_shared.redis_management import types as _types
from autobot_shared.redis_management.config import RedisConfigLoader
from autobot_shared.redis_management.connection_manager import RedisConnectionManager
from autobot_shared.redis_management.database_names import database_number
from autobot_shared.redis_management.database_overrides import apply_overrides, override_problems

#: main moves to an undeclared number, knowledge onto main's vacated one.
_OVERRIDES = {"AUTOBOT_REDIS_DB_MAIN": "12", "AUTOBOT_REDIS_DB_KNOWLEDGE": "0"}
_ENV = ("REDIS_HOST", "AUTOBOT_REDIS_HOST", "REDIS_PORT", "AUTOBOT_REDIS_PORT", "REDIS_PASSWORD")
_ENV += ("AUTOBOT_REDIS_PASSWORD", "REDIS_USERNAME", "AUTOBOT_REDIS_USERNAME", "AUTOBOT_REDIS_TLS_ENABLED")


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


def _fresh_manager(monkeypatch) -> RedisConnectionManager:
    """A new canonical manager with no backend config manager (no network I/O at init)."""
    monkeypatch.setattr(_endpoint, "get_config_manager", lambda: None)
    RedisConnectionManager.reset_instance()
    return RedisConnectionManager()


@pytest.fixture
def manager(monkeypatch):
    yield _fresh_manager(monkeypatch)
    RedisConnectionManager.reset_instance()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    import autobot_shared.ssot_config as ssot_config

    ssot_config.get_config.cache_clear()
    yield
    ssot_config.get_config.cache_clear()


# --- name -> number ---------------------------------------------------------------


def test_the_override_moves_main_and_knowledge_and_their_aliases(overridden):
    assert (database_number("main"), database_number("knowledge")) == (12, 0)
    assert (database_number("memory"), database_number("facts")) == (12, 0), "aliases follow their target"


def test_the_canonical_client_follows_the_override(overridden, manager):
    assert manager.resolve_db_config("main").db == 12
    assert manager.resolve_db_config("knowledge").db == 0


def test_a_yaml_entry_takes_its_number_from_the_one_table(overridden):
    endpoint = {"host": "127.0.0.1", "port": 6379}
    assert RedisConfigLoader._parse_database_config("knowledge", {"db": 1}, endpoint).db == 0


def test_the_legacy_tasks_variable_still_moves_workflows():
    moved = apply_overrides(_types.DECLARED_DATABASES, {"AUTOBOT_REDIS_DB_TASKS": "12"})
    assert moved["workflows"] == 12


def test_an_override_that_merges_two_databases_is_refused_and_named():
    """AUTOBOT_REDIS_DB_TESTING=15 (an old overlay's value) is celery_results' DB."""
    source = {"AUTOBOT_REDIS_DB_TESTING": "15"}
    assert apply_overrides(_types.DECLARED_DATABASES, source)["testing"] == _types.DECLARED_DATABASES["testing"]
    assert override_problems(_types.DECLARED_DATABASES, source) == [
        "AUTOBOT_REDIS_DB_TESTING=15 collides with celery_results"
    ]


def test_a_non_numeric_override_is_refused():
    assert override_problems(_types.DECLARED_DATABASES, {"AUTOBOT_REDIS_DB_MAIN": "zero"}) == [
        "AUTOBOT_REDIS_DB_MAIN='zero' is not a Redis database number"
    ]


# --- host / port / auth ------------------------------------------------------------


def _assert_url_matches_client(manager, database: str) -> None:
    from autobot_shared.ssot_config import get_config

    url = urlparse(get_config().get_redis_url_for_db(database_number(database)))
    client = manager.resolve_db_config(database)
    assert (url.hostname, url.port, url.path) == (client.host, client.port, f"/{client.db}")
    password = unquote(url.password) if url.password is not None else None
    assert (url.username or None, password) == (client.username, client.password)
    assert url.scheme == ("rediss" if client.ssl else "redis")


@pytest.fixture
def _reset_after():
    yield
    RedisConnectionManager.reset_instance()


def test_url_names_the_canonical_clients_host_when_the_tiers_disagree(monkeypatch, _reset_after):
    """NetworkConstants answers before the SSOT field; the URL used to skip straight to SSOT."""
    monkeypatch.setattr(_endpoint.NetworkConstants, "REDIS_VM_IP", "10.20.30.40", raising=False)
    monkeypatch.setenv("AUTOBOT_REDIS_PASSWORD", "p@ss")  # pragma: allowlist secret
    monkeypatch.setenv("AUTOBOT_REDIS_USERNAME", "svc")
    fresh = _fresh_manager(monkeypatch)
    assert fresh.resolve_db_config("knowledge").host == "10.20.30.40"
    for database in ("main", "knowledge", "celery_broker"):
        _assert_url_matches_client(fresh, database)


def test_url_and_client_agree_on_tls(monkeypatch, _reset_after):
    monkeypatch.setenv("AUTOBOT_REDIS_TLS_ENABLED", "true")
    monkeypatch.setenv("AUTOBOT_REDIS_TLS_PORT", "6390")
    fresh = _fresh_manager(monkeypatch)
    assert fresh.resolve_db_config("main").port == 6390
    _assert_url_matches_client(fresh, "main")


def test_url_follows_a_moved_database(overridden, manager):
    _assert_url_matches_client(manager, "knowledge")
    _assert_url_matches_client(manager, "main")


def test_network_constants_database_numbers_follow_the_override(overridden):
    from autobot_shared.network_constants import DatabaseConstants

    assert (DatabaseConstants.MAIN_DB, DatabaseConstants.KNOWLEDGE_DB) == (12, 0)
    assert DatabaseConstants.TASKS_DB == database_number("workflows")


def test_the_number_table_and_the_tuning_loader_read_one_yaml(monkeypatch):
    """One resolver: the loader's default path is exactly the table's (#19232)."""
    monkeypatch.setattr(_types, "_yaml_candidates", lambda: ["/nonexistent/redis-databases.yaml"])
    assert _types.resolve_databases_yaml() is None
    assert RedisConfigLoader._resolve_yaml_path(None) is None
