# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""add_ansible_name_unique_constraint on a fresh and on an existing DB (#2011, #19371).

A fresh DB runs the migrations before create_all builds ``nodes``. The old
migration issued ``CREATE UNIQUE INDEX ... ON nodes`` unconditionally, which
raised ``relation "nodes" does not exist``; once the SLM entrypoint made a
migration error fatal (#19371) that stopped the container from starting.
"""

from __future__ import annotations

from migrations import add_ansible_name_unique_constraint as migration
from migrations import utils as migration_utils


class _Cursor:
    """Answers the two existence probes the migration issues and records DDL."""

    def __init__(self, has_nodes: bool, has_unique: bool):
        self._has_nodes = has_nodes
        self._has_unique = has_unique
        self._row = None
        self.ddl: list = []

    def execute(self, sql, params=None):
        if "information_schema.tables" in sql:
            self._row = (self._has_nodes and params == ("nodes",),)
        elif "pg_index" in sql:
            self._row = (self._has_unique,)
        else:
            self.ddl.append(sql)
            self._row = None

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, cursor: _Cursor):
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def _run(monkeypatch, has_nodes: bool, has_unique: bool):
    cursor = _Cursor(has_nodes, has_unique)
    conn = _Connection(cursor)
    monkeypatch.setattr(migration, "get_connection", lambda db_url: conn)
    migration_utils.reset_deferrals()
    migration.migrate("postgresql://unused")
    return cursor, conn


def test_fresh_db_defers_instead_of_failing(monkeypatch):
    cursor, conn = _run(monkeypatch, has_nodes=False, has_unique=False)
    assert cursor.ddl == []
    assert migration_utils.deferrals() == ["nodes.uq_nodes_ansible_name"]
    assert conn.closed


def test_existing_db_without_unique_index_creates_it(monkeypatch):
    cursor, conn = _run(monkeypatch, has_nodes=True, has_unique=False)
    assert len(cursor.ddl) == 1
    assert cursor.ddl[0].startswith("CREATE UNIQUE INDEX uq_nodes_ansible_name ON nodes (ansible_name)")
    assert migration_utils.deferrals() == []
    assert conn.committed and conn.closed


def test_existing_unique_index_is_not_duplicated(monkeypatch):
    """create_all's nodes_ansible_name_key, or this migration's own index."""
    cursor, conn = _run(monkeypatch, has_nodes=True, has_unique=True)
    assert cursor.ddl == []
    assert migration_utils.deferrals() == []
    assert conn.committed and conn.closed
