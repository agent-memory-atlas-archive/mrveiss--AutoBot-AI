# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Execute one node's ordered post-sync plan, recording every action (#17455).

The caller (api/node_post_sync.py) builds the plan -- every role's actions through
``ordered_post_sync_plan`` -- and this module only runs it, top to bottom, through
``post_sync_runner``. One implementation of "run post-sync work": the node route and
the role route both come here.

Failure semantics (the same as the per-role path before #17455, which returned on a
failed post-sync before restarting or marking the role synced):

* a failed install/schema/build/post-sync command ABORTS the plan: nothing after it
  runs, restarts included, and the caller marks no role on the node synced;
* a failed RESTART is recorded and logged but does NOT abort or fail the sync
  (owner decision 3: "records failures"); its row carries ``success=False``.

The self-killing unit (``self_units``, passed in by the caller from ``_post_sync_plan``)
restarts last by ordering. Every earlier row is already committed (``record_action_run``
commits per row), and its own row is written as *issued* (``success=None``) BEFORE the
restart is sent, because the restart may end this process before an outcome exists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Iterable, Sequence

from autobot_shared.ssot_config import config
from services.post_sync_recorder import ActionOutcome, RunContext, record_action_run
from services.post_sync_runner import (
    recorded_restart_command,
    restart_ssh_base,
    restart_units,
    run_post_sync_command,
    units_are_valid,
)
from services.post_sync_timeouts import POST_SYNC_TIMEOUT_S
from services.ssh_utils import build_ssh_base_cmd

logger = logging.getLogger(__name__)

_RESTART = "restart"


@dataclass(frozen=True)
class NodeConn:
    """How to reach one node over SSH."""

    node_id: str
    ip: str
    user: str
    port: int


@dataclass
class PlanResult:
    """Recorded rows in execution order; ``aborted`` when a non-restart action failed."""

    rows: list = field(default_factory=list)
    aborted: bool = False
    skipped: list = field(default_factory=list)  # labels of actions never run because of the abort


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _run_command(action, conn: NodeConn, rec: RunContext) -> ActionOutcome:
    ssh_base = build_ssh_base_cmd(conn.ip, conn.user, conn.port, config.path.ssh_key_path)
    return await run_post_sync_command(
        ssh_base, action.command, POST_SYNC_TIMEOUT_S, rec, action.label, action.category
    )


async def _issue_self_restart(ssh_base: list, units: Sequence[str], rec: RunContext) -> ActionOutcome:
    """Commit an *issued* row, then send the restart that may end this process."""
    joined = " ".join(units)
    now = _now()
    msg = "issued; outcome unknown (this restart may end the SLM process before one exists)"
    issued = ActionOutcome(None, None, "", msg, now, now)
    command = recorded_restart_command(units)
    row = await record_action_run(rec, _RESTART, f"Restart {joined}", command, joined, issued)
    await restart_units(ssh_base, units, None)  # a surviving return is logged by restart_units
    return replace(issued, row=row)


async def _run_restart(action, conn: NodeConn, rec: RunContext, self_units: Iterable[str]) -> ActionOutcome:
    units = action.systemd_service.split()
    ssh_base = restart_ssh_base(conn.ip, conn.user, conn.port)
    if all(unit in self_units for unit in units) and units_are_valid(units):
        return await _issue_self_restart(ssh_base, units, rec)
    return await restart_units(ssh_base, units, rec)


async def _run_action(action, conn: NodeConn, rec: RunContext, self_units: Iterable[str]) -> ActionOutcome:
    if action.category == _RESTART:
        return await _run_restart(action, conn, rec, self_units)
    return await _run_command(action, conn, rec)


async def execute_plan(
    actions: Iterable, conn: NodeConn, rec: RunContext, self_units: Iterable[str] = ()
) -> PlanResult:
    """Run *actions* in the order given; see the module docstring for failure semantics."""
    result = PlanResult()
    ran: set[tuple] = set()
    pending = list(actions)
    for index, action in enumerate(pending):
        key = (action.role_name, action.command)
        if action.category != _RESTART and key in ran:
            continue  # one role's command can classify as schema AND build; run it once
        ran.add(key)
        outcome = await _run_action(action, conn, replace(rec, role_name=action.role_name), self_units)
        if outcome.row is not None:
            result.rows.append(outcome.row)
        if action.category != _RESTART and not outcome.success:
            result.aborted = True
            result.skipped = [a.label for a in pending[index + 1 :]]
            break
    return result
