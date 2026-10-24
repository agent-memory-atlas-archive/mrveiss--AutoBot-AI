# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Add the missing llc_workspace_leases.created_at / updated_at (#17996).

Revision ID: 20261024_103
Revises: 20261012_102
Create Date: 2026-10-24 00:00:01.000000

``Base`` gives every mapped model ``created_at`` and ``updated_at``. Migration 093 created
``llc_workspace_leases`` with the eleven columns the model spells out and neither of those,
so on a migrated database every ``SELECT`` of ``LLCWorkspaceLease`` -- the lease sweep and
``release_for_run`` -- failed with ``column llc_workspace_leases.created_at does not exist``
and no heartbeat run could finish.

``NO DATA LOSS``: additive only. Each column is added with the same type, timezone,
nullability and ``now()`` server default ``Base`` declares, so existing rows are filled with
the time of the migration and nothing is rewritten or deleted. Idempotent (``has_column``)
for a database built by ``create_all``, which already has both. ``downgrade`` drops exactly
these two columns.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migrations.guards import has_column, has_table

revision: str = "20261024_103"
down_revision: Union[str, None] = "20261012_102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "llc_workspace_leases"


def upgrade() -> None:
    if not has_table(_TABLE):
        return
    # Literal add_column calls (not a loop) so the baseline probe ladder can observe
    # this revision; has_column keeps each one idempotent for a create_all database.
    if not has_column("llc_workspace_leases", "created_at"):
        op.add_column(
            "llc_workspace_leases",
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
    if not has_column("llc_workspace_leases", "updated_at"):
        op.add_column(
            "llc_workspace_leases",
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )


def downgrade() -> None:
    # Drops only the two columns this revision adds, and only when present.
    for column in ("updated_at", "created_at"):
        if has_column(_TABLE, column):
            op.drop_column(_TABLE, column)
