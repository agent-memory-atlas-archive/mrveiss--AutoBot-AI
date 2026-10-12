# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Provider-key hydration for every process that is not the backend lifespan (#18094).

The lifespan hydrates in Phase 1. Every other process that resolves a provider key -- a
Celery worker, a CLI or operator script that builds a KnowledgeBase or an LLM provider, the
knowledge-base MCP server, anything importing the backend outside uvicorn -- would otherwise
resolve with the vault unread and serve the environment for a key the vault holds. Two hooks
close that:

* :func:`hydrate_this_process` runs once on first resolution when a root key is configured
  (``provider_key_vault.resolve_provider_key`` calls it), so no entry point needs to remember.
* ``celery_app`` also calls it from ``worker_process_init``, so a worker reads the vault before
  its first task rather than on it.

It runs on ``user_management.database.isolated_session_factory`` (unpooled, disposed) in a private
event loop on its own thread:
safe to call from sync code or from inside a running loop, and it never leaves connections
bound to a loop that is about to close. It never raises (``hydrate_at_startup`` classifies a
failure as an outage, which refuses keys).
"""

from __future__ import annotations

import asyncio
import threading

from autobot_shared.logging_manager import get_logger

logger = get_logger(__name__)

_lock = threading.Lock()


def hydrate_this_process() -> list[str]:
    """Hydrate once per process; later calls (and concurrent first calls) return without re-reading."""
    from services import provider_key_vault as vault

    with _lock:
        if vault._vault_state != vault.VAULT_NOT_HYDRATED:
            return []
        result: list[list[str]] = []
        worker = threading.Thread(
            target=lambda: result.append(asyncio.run(_hydrate_on_private_engine())), name="provider-key-hydration"
        )
        worker.start()
        worker.join()
        if not result:  # the thread died outside hydrate_at_startup's classification: fail closed
            vault._vault_state = vault.VAULT_UNREACHABLE
            logger.error("provider-key hydration thread failed; every vault-resolved provider key is refused")
            return []
        return result[0]


async def _hydrate_on_private_engine() -> list[str]:
    from services.provider_key_vault import hydrate_at_startup
    from user_management.database import isolated_session_factory

    result: list[str] | None = None
    try:
        async with isolated_session_factory() as factory:
            result = await hydrate_at_startup(lambda: factory)
    except Exception as exc:  # noqa: BLE001 - classified below, never raised
        if result is not None:  # hydration finished; only disposing the private engine failed
            logger.warning("provider-key hydration: private engine dispose failed (%s)", type(exc).__name__)
            return result
        return await hydrate_at_startup(_raise(exc))  # building the engine is the DB too: an outage
    return result


def _raise(exc: Exception):
    """A session-factory getter that re-raises *exc*, so hydrate_at_startup classifies it as an outage."""

    def _getter():
        raise exc

    return _getter
