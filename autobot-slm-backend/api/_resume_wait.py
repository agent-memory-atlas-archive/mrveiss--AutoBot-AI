# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Hold the resumed fleet stage until the detached self-update has ended (#16644).

The self-update runs in a transient systemd unit (``playbook_executor``) so it
outlives the SLM restart its own Play 1 performs. The restarted process finds
the resume plan and used to start the fleet stage at once -- while the unit was
still running its later plays. Those plays restart the SLM a second time, which
killed the in-flight (attached) node deploy with code -15.

Only the no-restart path (``_await_self_update_completion``) ever waited. This
is the restart path's wait. It asks systemd whether the unit is active
(``read_deploy_activity``) instead of parsing log text: the log carries a PLAY
RECAP only at the very end, but a mid-run marker is easy to misread.

``in_progress is None`` (unit could not be queried) degrades to the previous
behaviour -- proceed -- because unknown is neither "finished" nor a reason to
hang the resume forever.

Split out of ``api/code_sync.py``, which sits at its size ceiling.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from api._resume_plan import _clear_resume_plan
from autobot_shared.time_utils import utc_timestamp
from services.deploy_activity import read_deploy_activity

if TYPE_CHECKING:  # pragma: no cover - typing only
    from api.code_sync import UpdateAllJob

# Plain stdlib logging for the same reason as api/_resume_plan.py.
logger = logging.getLogger(__name__)


def _fail_resume(job: "UpdateAllJob", reason: str) -> None:
    """Fail the job and its self-update stage with *reason*."""
    now = utc_timestamp()
    for stage in job.stages:
        if stage.name == "slm_self_update":
            stage.status = "failed"
            stage.message = reason[:300]
            stage.completed_at = now
    job.status = "failed"
    job.failure_reason = reason[:300]
    job.completed_at = now


async def wait_for_detached_self_update(
    job: "UpdateAllJob",
    mark_progress,
    timeout_s: float,
    poll_s: float,
) -> bool:
    """Block while the detached self-update unit is active.

    Returns True when the fleet stage may start (unit ended, or its state is
    unknown). Returns False after failing the job and clearing the plan when the
    unit is still active after *timeout_s*. Each poll stamps progress so the
    staleness rule does not retire the job mid-wait (#14703).
    """
    deadline = time.monotonic() + timeout_s
    announced = False
    while True:
        activity = await read_deploy_activity()
        if activity.in_progress is not True:
            if activity.in_progress is None:
                logger.warning("update-all resume: self-update unit not queryable; not waiting (%s)", activity.reason)
            return True
        if not announced:
            logger.info("update-all resume: detached self-update still running; holding the fleet stage")
            announced = True
        mark_progress(job)
        if time.monotonic() >= deadline:
            reason = f"detached self-update still running after {timeout_s}s — not continuing to the fleet stage"
            logger.error("update-all resume: %s", reason)
            _fail_resume(job, reason)
            await _clear_resume_plan()
            return False
        await asyncio.sleep(poll_s)
