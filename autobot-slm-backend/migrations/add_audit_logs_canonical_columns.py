# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""
Migration: add the canonical audit-trail columns to audit_logs (#17225 D1)

``autobot_shared/user_management/models/audit.py :: AuditLog`` gained
``category``, ``actor``, ``session_id`` and ``resource_ref`` when ``audit_logs``
became the audit trail's system of record. The SLM maps that same model onto
its ``slm_users`` database, and ``create_all`` never adds a column to an
existing table, so without this migration every SLM insert into ``audit_logs``
would name columns its table does not have. Mirrors the backend's Alembic
revision ``20261011_098``.

NO DATA LOSS: four additive columns. ``category`` is NOT NULL with DEFAULT
'security' (a catalogue default on PostgreSQL 11+, no row rewrite); the other
three are nullable. Idempotent via ``add_column_if_not_exists``.

TARGET_DB routes this to the user_management database (#14300), the only one
that holds ``audit_logs``.

Run: python3 -m migrations.add_audit_logs_canonical_columns
"""

import logging
import sys

from migrations.runner import TARGET_DB_USER_MANAGEMENT
from migrations.utils import add_column_if_not_exists, get_connection, table_exists

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

TARGET_DB = TARGET_DB_USER_MANAGEMENT

_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS ix_audit_logs_category ON audit_logs (category)",
    "CREATE INDEX IF NOT EXISTS idx_audit_actor_created ON audit_logs (actor, created_at)",
)


def migrate(db_url: str) -> None:
    """Add the four canonical columns and their indexes (#17225)."""
    logger.info("Running migration: add_audit_logs_canonical_columns")

    conn = get_connection(db_url)
    cursor = conn.cursor()

    add_column_if_not_exists(cursor, "audit_logs", "category", "VARCHAR(32) NOT NULL DEFAULT 'security'")
    add_column_if_not_exists(cursor, "audit_logs", "actor", "VARCHAR(255)")
    add_column_if_not_exists(cursor, "audit_logs", "session_id", "VARCHAR(255)")
    add_column_if_not_exists(cursor, "audit_logs", "resource_ref", "VARCHAR(512)")
    if table_exists(cursor, "audit_logs"):  # a fresh DB gets the indexes from create_all
        for statement in _INDEX_SQL:
            cursor.execute(statement)

    conn.commit()
    conn.close()

    logger.info("Migration complete: audit_logs carries category/actor/session_id/resource_ref")


if __name__ == "__main__":
    from migrations.runner import get_user_management_db_url

    db_url = sys.argv[1] if len(sys.argv) > 1 else get_user_management_db_url()
    migrate(db_url)
