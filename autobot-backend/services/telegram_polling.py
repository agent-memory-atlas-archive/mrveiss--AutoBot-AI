# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Outbound long-polling ingest for the Telegram bot (#19010).

The webhook needs Telegram to reach AutoBot over public HTTPS, which a local,
LAN-only or NAT install cannot offer. Polling inverts the direction: AutoBot
asks Telegram for updates with ``getUpdates``, so no inbound route is needed.

The ingest mode is one stored value, ``webhook`` or ``polling``, so the two can
never both be live: selecting polling deletes the webhook, selecting webhook
stops the poller at its next tick (and Telegram itself refuses ``getUpdates``
while a webhook is set).

Exactly one backend worker polls, under a :class:`LeaderLease` -- two pollers on
one token take updates from each other and Telegram answers the second with 409.
Each update goes through the same ``process_telegram_update`` the webhook calls,
so there is one routing path, not two.

Delivery is at-most-once: the offset is persisted before an update's reply is
generated, so a restart never replays (and never re-answers) a message, at the
cost of dropping one whose reply was in flight when the worker died.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Dict, List

import aiohttp

from autobot_shared.http_client import get_http_client
from autobot_shared.leader_lease import DEFAULT_TTL_MS, LeaderLease
from autobot_shared.logging_manager import get_logger
from autobot_shared.redis_client import get_redis_client
from services.telegram_bot_service import TelegramBotService

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = get_logger(__name__)

TELEGRAM_INGEST_MODE_KEY = "autobot:settings:telegram_ingest_mode"
TELEGRAM_POLL_OFFSET_KEY = "autobot:telegram:poll_offset"
TELEGRAM_POLL_LEASE_KEY = "autobot:telegram:poll_leader"

INGEST_MODE_WEBHOOK = "webhook"
INGEST_MODE_POLLING = "polling"
INGEST_MODES = (INGEST_MODE_WEBHOOK, INGEST_MODE_POLLING)

#: The lease is renewed at the start of every tick and each tick is one long
#: poll, so the window must end well inside the lease lifetime -- otherwise a
#: second worker could take the lease while this one is still polling.
POLL_TIMEOUT_S = max(1, DEFAULT_TTL_MS // 1000 - 10)
#: Pause between ticks. The long poll itself is the wait, so this only spaces
#: out back-to-back batches.
LEASE_REFRESH_S = 1.0
#: The shared client's 10s socket-read limit would cut every long poll short.
_POLL_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=POLL_TIMEOUT_S + 10, sock_read=POLL_TIMEOUT_S + 5)

#: Only message updates are routed (``process_telegram_update`` ignores the rest),
#: so asking Telegram for anything else would only advance the offset past it.
_ALLOWED_UPDATES = ["message"]


async def get_ingest_mode() -> str:
    """The stored ingest mode; ``webhook`` when never set, as before polling existed."""
    redis = await get_redis_client()
    if redis is None:
        return INGEST_MODE_WEBHOOK
    mode = await redis.get(TELEGRAM_INGEST_MODE_KEY)
    mode = mode.decode("utf-8") if isinstance(mode, bytes) else mode
    return mode if mode in INGEST_MODES else INGEST_MODE_WEBHOOK


async def save_ingest_mode(mode: str) -> None:
    """Persist the ingest mode. Raises ValueError for an unknown mode."""
    if mode not in INGEST_MODES:
        raise ValueError(f"Unknown Telegram ingest mode: {mode!r}")
    redis = await get_redis_client()
    if redis is None:
        raise RuntimeError("Redis client not available")
    await redis.set(TELEGRAM_INGEST_MODE_KEY, mode)


async def _load_offset(redis: Any) -> int:
    raw = await redis.get(TELEGRAM_POLL_OFFSET_KEY)
    return int(raw) if raw else 0


async def _call_bot_api(service: TelegramBotService, method: str, payload: Dict[str, Any], **kwargs: Any) -> Any:
    """POST one Bot API method; returns ``result`` or raises on ``ok: false``."""
    if not service.bot_token or not service.base_url:
        raise ValueError("Telegram bot token not configured")
    url = f"{service.base_url}/{method}"
    async with get_http_client().tracked_request("POST", url, json=payload, **kwargs) as response:
        body = await response.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {body.get('description')}")
    return body.get("result")


async def delete_webhook(service: TelegramBotService) -> None:
    """Remove any registered webhook so ``getUpdates`` is allowed."""
    await _call_bot_api(service, "deleteWebhook", {"drop_pending_updates": False})
    logger.info("Deleted Telegram webhook for polling ingest")


async def fetch_updates(service: TelegramBotService, offset: int) -> List[Dict[str, Any]]:
    """One ``getUpdates`` long-poll starting at *offset*."""
    payload = {"offset": offset, "timeout": POLL_TIMEOUT_S, "allowed_updates": _ALLOWED_UPDATES}
    return await _call_bot_api(service, "getUpdates", payload, timeout=_POLL_HTTP_TIMEOUT) or []


async def poll_once(app: "FastAPI") -> int:
    """Fetch and dispatch one batch; returns how many updates were dispatched.

    The offset is saved before each update is handed off, so a crash mid-reply
    cannot cause the same message to be answered twice after restart.
    """
    if await get_ingest_mode() != INGEST_MODE_POLLING:
        return 0
    redis = await get_redis_client()
    service = await TelegramBotService.from_redis()
    if redis is None or not service.bot_token:
        return 0
    updates = await fetch_updates(service, await _load_offset(redis))
    for update in updates:
        await redis.set(TELEGRAM_POLL_OFFSET_KEY, int(update["update_id"]) + 1)
        _dispatch(app, update)
    return len(updates)


def _dispatch(app: "FastAPI", update: Dict[str, Any]) -> None:
    """Hand one update to the shared routing path without blocking the poll.

    A reply can take as long as the LLM does; awaiting it here would hold the
    poller past its lease and let a second worker start polling the same token.
    """
    from api.telegram_bot import process_telegram_update

    tasks = app.state.__dict__.setdefault("telegram_update_tasks", set())
    task = asyncio.create_task(process_telegram_update(app, update), name="telegram-update")
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def start(app: "FastAPI") -> None:
    """Start the leader-elected poller (NON-CRITICAL); idle unless mode is polling."""
    lease = LeaderLease(key=TELEGRAM_POLL_LEASE_KEY, refresh_s=LEASE_REFRESH_S, label="telegram-poller")

    async def _tick() -> None:
        await poll_once(app)

    async def _run() -> None:
        try:
            await lease.run(on_tick=_tick)
        finally:
            # Hand the lease over at once on shutdown rather than after its TTL.
            await lease.release()

    app.state.telegram_poller_task = asyncio.create_task(_run(), name="telegram-poller")
    logger.info("Telegram poller started (active only in polling ingest mode)")


__all__ = [
    "INGEST_MODES",
    "INGEST_MODE_POLLING",
    "INGEST_MODE_WEBHOOK",
    "delete_webhook",
    "fetch_updates",
    "get_ingest_mode",
    "poll_once",
    "save_ingest_mode",
    "start",
]
