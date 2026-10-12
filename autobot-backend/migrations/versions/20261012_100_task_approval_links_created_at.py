# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Add the missing task_approval_links.created_at (Base timestamp drift) (#13421).

Revision ID: 20261012_100
Revises: 20261012_099
Create Date: 2026-10-12 00:00:01.000000

``Base`` gives every mapped model ``created_at`` and ``updated_at``. Migration 006 created
``task_approval_links`` with neither and 063 (#10636) added only ``updated_at``, so on a
freshly migrated database every ``selectinload(Approval.task_links)`` -- which is what
``ApprovalGateService.list_approvals`` and the new ``list_pending`` do -- fails with
``column task_approval_links.created_at does not exist``. Found by running the S1 inbox
query against ``alembic upgrade head``.

Idempotent (``IF NOT EXISTS`` + ``has_table``) like 063, so it is safe on a database whose
column was added by hand. Forward-only: dropping it would re-break the ORM read path.
"""

from typing import Sequence, Union

from alembic import op

from migrations.guards import has_table

revision: str = "20261012_100"
down_revision: Union[str, None] = "20261012_099"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if has_table("task_approval_links"):
        op.execute(
            "ALTER TABLE task_approval_links "
            "ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()"
        )


def downgrade() -> None:
    # Forward-only drift reconciliation (same as 063): dropping created_at again would
    # re-break the ORM read path this column fixes.
    pass
