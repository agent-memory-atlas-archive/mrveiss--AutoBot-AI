# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Run status transitions for a heartbeat run (#16818, #620).

Sits beside `run_context` and `run_workspace`: one small module per per-run concern,
rather than another few dozen lines inside the scheduler.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..models.enums import LLCRunStatus
from ..models.heartbeat_run import LLCHeartbeatRun

logger = logging.getLogger(__name__)

#: A run in one of these states is "this agent is already being worked" (#19582): an
#: event wake is not started beside it, the same rule replay and the comment wake had.
ACTIVE_RUN_STATUSES = (LLCRunStatus.QUEUED.value, LLCRunStatus.RUNNING.value)

#: Written to a run that lost the claim to another RUNNING run of the same agent.
SUPERSEDED_REASON = "superseded: the agent already has a RUNNING run (#19582)"


class RunSuperseded(Exception):
    """The run was not started: another run of the same agent is RUNNING (#19582)."""


async def latest_run_in(session: AsyncSession, agent_id: str, statuses: tuple) -> Optional[LLCHeartbeatRun]:
    """The newest run of *agent_id* whose status is in *statuses*, or ``None``.

    One finder for the scheduler's rate-limited resume and the event wake's
    active-run check, instead of one query per caller.
    """
    result = await session.execute(
        select(LLCHeartbeatRun)
        .where(LLCHeartbeatRun.agent_id == agent_id, LLCHeartbeatRun.status.in_(statuses))
        .order_by(LLCHeartbeatRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _claim_running(run_id: UUID) -> Any:
    """UPDATE that marks *run_id* RUNNING only while no OTHER run of its agent is RUNNING.

    This is the per-agent claim (#19582): a tick and an event can each create a run,
    but only one of them can hold RUNNING, so the two never run the agent at once.
    """
    other = aliased(LLCHeartbeatRun)
    busy = exists().where(
        other.agent_id == LLCHeartbeatRun.agent_id, other.status == LLCRunStatus.RUNNING.value, other.id != run_id
    )
    return (
        update(LLCHeartbeatRun)
        .where(LLCHeartbeatRun.id == run_id, ~busy)
        .values(status=LLCRunStatus.RUNNING.value, started_at=datetime.now(tz=timezone.utc))
        .execution_options(synchronize_session=False)
    )


async def _mark_superseded(session: AsyncSession, run_id: UUID) -> None:
    """Close out a run that lost the claim as SKIPPED, with the reason on the row."""
    await session.execute(
        update(LLCHeartbeatRun)
        .where(LLCHeartbeatRun.id == run_id)
        .values(status=LLCRunStatus.SKIPPED.value, finished_at=datetime.now(tz=timezone.utc), error=SUPERSEDED_REASON)
    )
    await session.commit()
    logger.info("Run %s not started: %s", run_id, SUPERSEDED_REASON)


async def mark_running(factory: Any, run_id: UUID) -> Optional[int]:
    """Read `retry_count`, then mark the run RUNNING. ``None`` if that failed.

        The read comes first so backoff is computed from the count before this attempt.
        On failure the run is marked FAILED here -- the status write belongs with the
        attempt that failed -- but the workspace release does NOT: the caller owns that,
        so that every exit from `_run_adapter` has its release where a reader of
        `_run_adapter` can see it. Extracted from that function to keep it inside the
        65-line limit (#620), and placed beside `run_context` and `run_workspace`, which hold the
    other per-run concerns.

    Raises :class:`RunSuperseded` (after marking the run SKIPPED) when another run of
    the same agent is already RUNNING -- see :func:`_claim_running` (#19582).
    """
    try:
        async with factory() as session:
            result = await session.execute(select(LLCHeartbeatRun.retry_count).where(LLCHeartbeatRun.id == run_id))
            retry_count: int = result.scalar_one_or_none() or 0
            claimed = await session.execute(_claim_running(run_id))
            if claimed.rowcount == 0:
                await _mark_superseded(session, run_id)
                raise RunSuperseded(SUPERSEDED_REASON)
            await session.commit()
        return retry_count
    except RunSuperseded:
        raise
    except Exception as exc:
        logger.exception("Failed to mark run %s as RUNNING — marking FAILED", run_id)
        try:
            async with factory() as session:
                await session.execute(
                    update(LLCHeartbeatRun)
                    .where(LLCHeartbeatRun.id == run_id)
                    .values(
                        status=LLCRunStatus.FAILED.value,
                        finished_at=datetime.now(tz=timezone.utc),
                        error=str(exc),
                    )
                )
                await session.commit()
        except Exception:
            logger.exception("Could not write FAILED status for run %s", run_id)
        return None
