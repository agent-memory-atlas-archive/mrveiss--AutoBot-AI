# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Startup import of the system-knowledge templates into the canonical KB (#19347).

The import (#18577 / #19216) used to run only from the operator CLI: ``get_knowledge_manager``
had no production caller, so a deployed backend never wrote the templates into its KB. This is
the one startup wiring point. Phase 2 calls :func:`start_system_knowledge_import` once, right
after the knowledge base is published.

* Non-blocking: the import runs as a retained background task (``fire_and_forget``).
* One worker: a :class:`LeaderLease` lets only one uvicorn worker import. The lease is renewed
  while the import runs and is left to expire afterwards, so workers that start in the same
  window skip instead of re-running it.
* Idempotent: ``SystemKnowledgeManager`` compares the template file-state hash and the per-item
  hashes, so a restart with unchanged templates makes no KB writes.
* A failure is logged and never reaches startup.
"""

from __future__ import annotations

import asyncio
from typing import Any

from autobot_shared.async_compat import fire_and_forget
from autobot_shared.leader_lease import LeaderLease
from autobot_shared.logging_manager import get_logger

logger = get_logger(__name__)

IMPORT_LEASE_KEY = "system_knowledge:import:leader"
IMPORT_TASK_NAME = "system-knowledge-import"


def import_lease() -> LeaderLease:
    """The lease that elects the one worker allowed to run the import."""
    return LeaderLease(key=IMPORT_LEASE_KEY, database="knowledge", label="System knowledge import")


def start_system_knowledge_import(app: Any) -> asyncio.Task | None:
    """Schedule the import once the KB is ready; returns the retained task, or None without a KB."""
    knowledge_base = getattr(app.state, "knowledge_base", None)
    if knowledge_base is None:
        logger.warning("System knowledge import skipped: knowledge base not ready")
        return None
    return fire_and_forget(run_system_knowledge_import(knowledge_base), name=IMPORT_TASK_NAME)


async def run_system_knowledge_import(knowledge_base: Any, lease: LeaderLease | None = None) -> bool:
    """Import the templates if this worker wins the lease. True only when the import ran cleanly."""
    lease = lease or import_lease()
    if not await lease.update_leadership():
        logger.info("System knowledge import: lease not acquired (another worker or no Redis), skipping")
        return False
    keeper = asyncio.create_task(_keep_lease(lease), name=f"{IMPORT_TASK_NAME}-lease")
    try:
        await _import(knowledge_base)
    except Exception as exc:
        logger.error("System knowledge import failed; startup continues: %s", exc, exc_info=True)
        return False
    finally:
        keeper.cancel()
    logger.info("System knowledge import finished")
    return True


async def _import(knowledge_base: Any) -> None:
    """Run the import through the one entry point, the ``KnowledgeManager`` facade."""
    from agents.knowledge_manager import get_knowledge_manager

    # Machine-aware mode adapts per-host copies and never imports the base templates; temporal
    # tracking is not part of the import. Both stay off here.
    manager = await asyncio.to_thread(
        get_knowledge_manager, knowledge_base, enable_temporal=False, enable_machine_aware=False
    )
    await manager.initialize()


async def _keep_lease(lease: LeaderLease) -> None:
    """Renew the lease while the import runs, so a slow import is not joined by a late worker."""
    while True:
        await asyncio.sleep(lease.refresh_s)
        if not await lease.update_leadership():
            return
