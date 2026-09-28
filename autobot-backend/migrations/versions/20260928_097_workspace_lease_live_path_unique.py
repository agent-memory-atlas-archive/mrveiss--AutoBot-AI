# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A workspace path is unique among LIVE leases, not for all time (#16818).

Migration 093 created ``uq_llc_workspace_leases_path`` as a table-wide UNIQUE. The
model's own comment beside that column says what was meant:

    #: Unique: two *live* leases on one directory is the collision the ledger exists
    #: to prevent, so the database refuses it outright.

The comment says *live*; the constraint said *ever*. Under the table-wide version a
path can be leased exactly once in the lifetime of the installation -- the first
release permanently burns that directory, and the second acquire on it fails with an
integrity error. Since workspace paths are derived from issue numbers and are reused
constantly, that is every path, on its second use.

Nothing depended on the stricter reading: at 093 nothing acquired a lease at all, so
no deployment can hold a released row whose path is wanted again. The replacement is
a partial unique index carrying the intent the comment always stated.

``NO DATA LOSS``: this drops a UNIQUE **constraint** and creates a weaker partial
unique index in its place. No column, table or row is touched -- every existing lease
row survives byte for byte, and the only thing removed is a restriction. A constraint
that is relaxed can lose nothing: every row that satisfied the table-wide UNIQUE also
satisfies the partial one, so no row can be rejected or dropped by the change.
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision: str = "20260928_097"
down_revision: Union[str, None] = "20260926_096"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "llc_workspace_leases"
_OLD_CONSTRAINT = "uq_llc_workspace_leases_path"
_NEW_INDEX = "uq_llc_workspace_leases_live_path"


def _has_table(bind) -> bool:
    return _TABLE in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql" or not _has_table(bind):
        return

    existing = {c["name"] for c in sa.inspect(bind).get_unique_constraints(_TABLE)}
    if _OLD_CONSTRAINT in existing:
        op.drop_constraint(_OLD_CONSTRAINT, _TABLE, type_="unique")

    indexes = {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}
    if _NEW_INDEX not in indexes:
        op.create_index(
            _NEW_INDEX,
            _TABLE,
            ["path"],
            unique=True,
            postgresql_where=sa.text("released_at IS NULL"),
        )
    logger.info("#16818: workspace paths are now unique among live leases only")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql" or not _has_table(bind):
        return

    indexes = {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}
    if _NEW_INDEX in indexes:
        op.drop_index(_NEW_INDEX, table_name=_TABLE)

    # Restoring the table-wide UNIQUE can fail where a path has been leased more than
    # once -- which is the normal state after this migration has been in use. Left to
    # fail loudly rather than silently dropping the released rows that conflict: a
    # downgrade that destroys audit history to satisfy a constraint is worse than one
    # that stops and says why.
    existing = {c["name"] for c in sa.inspect(bind).get_unique_constraints(_TABLE)}
    if _OLD_CONSTRAINT not in existing:
        op.create_unique_constraint(_OLD_CONSTRAINT, _TABLE, ["path"])
