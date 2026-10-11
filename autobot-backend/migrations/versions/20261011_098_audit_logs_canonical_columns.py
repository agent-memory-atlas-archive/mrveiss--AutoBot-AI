# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""``audit_logs`` becomes the audit trail's system of record (#17225 D1).

The owner chose to extend the existing ``audit_logs`` table rather than add a
second Postgres audit table. Its original columns could not hold what the
canonical ``AuditEvent`` records: an actor that is not a user row (an agent,
``system``), a resource that is not a UUID (a path, a fact id), the session,
and the category that selects the retention period.

``NO DATA LOSS``: four additive columns and two indexes. ``actor``,
``session_id`` and ``resource_ref`` are nullable with no default. ``category``
is NOT NULL with ``server_default 'security'``, which PostgreSQL 11+ applies
as a catalogue default without rewriting a row; every existing row is a
security record (logins, permission denials, org/team/user changes), so the
default is also the true value. Nothing existing is altered or dropped.

Source of truth: ``autobot_shared/user_management/models/audit.py :: AuditLog``.
The SLM's ``slm_users`` database maps the same model and gets the same columns
from ``autobot-slm-backend/migrations/add_audit_logs_canonical_columns.py``.

Idempotent: every step is guarded by ``has_column`` / ``has_index``.

Revision ID: 20261011_098
Revises: 20260928_097
Create Date: 2026-10-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migrations.guards import has_column, has_index

revision: str = "20261011_098"
down_revision: Union[str, None] = "20260928_097"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Dropped in reverse order by `downgrade`.
_COLUMN_NAMES = ("category", "actor", "session_id", "resource_ref")
_INDEXES = (("ix_audit_logs_category", ("category",)), ("idx_audit_actor_created", ("actor", "created_at")))


def upgrade() -> None:
    """Add the four canonical columns and their indexes, skipping any that exist.

    Literal ``op.add_column("audit_logs", sa.Column("<name>", ...))`` calls, so
    the probe ladder can observe this revision (see 20260926_096).
    """
    if not has_column("audit_logs", "category"):
        op.add_column(
            "audit_logs",
            sa.Column("category", sa.String(32), nullable=False, server_default="security"),
        )
    if not has_column("audit_logs", "actor"):
        op.add_column("audit_logs", sa.Column("actor", sa.String(255), nullable=True))
    if not has_column("audit_logs", "session_id"):
        op.add_column("audit_logs", sa.Column("session_id", sa.String(255), nullable=True))
    if not has_column("audit_logs", "resource_ref"):
        op.add_column("audit_logs", sa.Column("resource_ref", sa.String(512), nullable=True))
    for name, columns in _INDEXES:
        if not has_index("audit_logs", name):
            op.create_index(name, "audit_logs", list(columns))


def downgrade() -> None:
    """Drop exactly what this migration added: the indexes, then the columns."""
    for name, _columns in reversed(_INDEXES):
        if has_index("audit_logs", name):
            op.drop_index(name, table_name="audit_logs")
    for name in reversed(_COLUMN_NAMES):
        if has_column("audit_logs", name):
            op.drop_column("audit_logs", name)
