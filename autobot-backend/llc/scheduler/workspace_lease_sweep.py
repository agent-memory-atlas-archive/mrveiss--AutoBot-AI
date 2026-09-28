# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Celery-beat sweep: reclaim expired workspace leases and dispose of what landed (#16818).

The counterpart to the stalled-run sweep. That one closes out runs whose agent stopped
reporting; this one takes back workspaces whose *holder* stopped existing -- the case
that deadlocked the fleet on 2026-09-16, when three merged worktrees were held by a
session that was gone and one by an open PR, and nothing could release any of them.

Two passes, deliberately in this order and deliberately not merged:

1. **Reclaim.** Every lease past its deadline is handed back, with a reason. This frees
   capacity immediately and unconditionally -- it is a database write about a lease,
   and it cannot fail because a directory is in a state somebody dislikes.
2. **Dispose.** Each reclaimed workspace is then offered to ``workspace_disposal``,
   which removes it only on evidence that its work reached the remote.

Merging the two would mean a workspace with unpushed work either blocks the reclaim --
keeping the slot occupied, which is the original deadlock -- or gets removed to free
it, which loses the work. Separating them gives the only answer that is right in both
directions: **the slot comes back, the directory stays until it is provably safe.**

A workspace kept back is not debris and must not be swept later on the grounds that it
has been sitting there: it is unfinished work, and the disposal check says so every
time it declines.
"""

import asyncio
import logging

from celery import shared_task

from llc.services.workspace_disposal import DisposalVerdict, dispose_workspace
from llc.services.workspace_lease import reclaim_expired
from user_management.database import get_async_session_factory
from utils.celery_reliability import (
    CELERY_MAX_RETRIES,
    CELERY_RETRY_BACKOFF_MAX,
    CELERY_TRANSIENT_ERRORS,
    DeadLetterTask,
)

logger = logging.getLogger(__name__)


@shared_task(
    name="llc.scheduler.workspace_lease_sweep.run_workspace_lease_sweep",
    bind=True,
    base=DeadLetterTask,
    autoretry_for=CELERY_TRANSIENT_ERRORS,
    retry_backoff=True,
    retry_jitter=True,
    retry_backoff_max=CELERY_RETRY_BACKOFF_MAX,
    max_retries=CELERY_MAX_RETRIES,
)
def run_workspace_lease_sweep(self: object) -> dict:  # type: ignore[type-arg]
    """Celery entry point. Returns the counts so a run is legible from the result."""
    return asyncio.run(_async_sweep())


async def _async_sweep() -> dict:
    """Reclaim expired leases, then dispose of the workspaces that demonstrably landed."""
    factory = get_async_session_factory()
    async with factory() as session:
        reclaimed = await reclaim_expired(session)
        # The reclaim is committed before any directory is touched. If disposal
        # crashes halfway, the slots are already back and the next sweep retries only
        # the disposal -- the expensive half is the one that must not be lost.
        candidates = [(lease.path, lease.branch) for lease in reclaimed]
        await session.commit()

    disposed = 0
    kept = 0
    for path, branch in candidates:
        check = await dispose_workspace(path, branch)
        if check.verdict is DisposalVerdict.LANDED:
            disposed += 1
        else:
            kept += 1

    # Every number reported, including the zeros: a sweep that found nothing and a
    # sweep that did not run must not look the same in the logs.
    logger.info(
        "#16818: workspace sweep reclaimed %d lease(s), disposed of %d workspace(s), kept %d",
        len(candidates),
        disposed,
        kept,
    )
    return {"reclaimed": len(candidates), "disposed": disposed, "kept": kept}


__all__ = ["run_workspace_lease_sweep"]
