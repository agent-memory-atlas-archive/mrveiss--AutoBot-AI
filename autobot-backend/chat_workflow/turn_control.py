# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Lifecycle of one LangGraph chat turn: run, bound, cancel (#17273).

A turn used to be a bare ``asyncio.create_task`` drained through a queue, with
nothing between the consumer and the task. When the SSE client went away the
consumer unwound and the task kept going -- more LLM calls and tool executions
for a reply nobody would receive -- and nothing bounded how long it could run.

This module is the one place a turn starts and ends:

* the consumer always cancels and awaits the task on the way out, so a client
  disconnect stops the run and the cancellation reaches the in-flight LLM call;
* the run carries a total timeout (``AUTOBOT_CHAT_TURN_TIMEOUT_SECONDS``);
* a registry keyed by session lets a client-callable cancel stop the CURRENT
  turn through the same teardown path;
* each ending is logged with its reason, so a cancellation is distinguishable
  from a completion and from a failure.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict

from async_chat_workflow import WorkflowMessage
from autobot_shared.env_utils import env_float
from autobot_shared.logging_manager import get_logger

logger = get_logger(__name__)

#: Total wall-clock budget for one graph turn (#17273). Read as a module
#: attribute at the point of use so a test (or a reload) sees one value.
CHAT_TURN_TIMEOUT_SECONDS: float = env_float("AUTOBOT_CHAT_TURN_TIMEOUT_SECONDS", default=1800.0)

#: Total timeout for one streaming LLM request, connect included (#17273).
CHAT_LLM_CALL_TIMEOUT_SECONDS: float = env_float("AUTOBOT_CHAT_LLM_CALL_TIMEOUT_SECONDS", default=600.0)

REASON_DISCONNECT = "client_disconnect"
REASON_CLIENT_CANCEL = "client_cancel"
CANCELLED_MESSAGE = "Generation stopped."
TIMEOUT_MESSAGE = "The model is taking too long to respond. Please try again."
ERROR_MESSAGE = "An unexpected error occurred. Please try again."


@dataclass
class _Turn:
    """One running turn: its task and why it was (or will be) cancelled."""

    session_id: str
    task: "asyncio.Task[None] | None" = None
    reason: str = REASON_DISCONNECT


@dataclass
class _Registry:
    turns: Dict[str, list] = field(default_factory=dict)

    def add(self, turn: _Turn) -> None:
        self.turns.setdefault(turn.session_id, []).append(turn)

    def remove(self, turn: _Turn) -> None:
        live = self.turns.get(turn.session_id, [])
        if turn in live:
            live.remove(turn)
        if not live:
            self.turns.pop(turn.session_id, None)


_REGISTRY = _Registry()


def has_active_turn(session_id: str) -> bool:
    """True while a graph turn for *session_id* is running in this process."""
    return bool(_REGISTRY.turns.get(session_id))


async def cancel_turn(session_id: str, reason: str = REASON_CLIENT_CANCEL) -> int:
    """Cancel every running turn of *session_id* and wait for the teardown.

    Returns how many turns were cancelled; 0 means there was nothing to cancel
    (idempotent -- a second call after the first is a clean no-op).
    """
    turns = [t for t in _REGISTRY.turns.get(session_id, []) if t.task is not None and not t.task.done()]
    for turn in turns:
        turn.reason = reason
        turn.task.cancel()
    if turns:
        await asyncio.gather(*(t.task for t in turns), return_exceptions=True)
    return len(turns)


async def _drop_checkpoint(session_id: str) -> None:
    """Issue #1475: a half-run checkpoint would poison the session's next turn."""
    from .graph import delete_thread_checkpoints

    try:
        await delete_thread_checkpoints(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not drop checkpoint for session %s: %s", session_id, exc)


async def _finish_cancelled(turn: _Turn, queue: asyncio.Queue) -> None:
    logger.info("Chat turn cancelled: session=%s reason=%s", turn.session_id, turn.reason)
    await _drop_checkpoint(turn.session_id)
    queue.put_nowait({"type": "cancelled", "content": CANCELLED_MESSAGE, "metadata": {"reason": turn.reason}})


async def _finish_failed(turn: _Turn, queue: asyncio.Queue, exc: Exception) -> None:
    timed_out = isinstance(exc, (TimeoutError, asyncio.TimeoutError))
    if timed_out:
        logger.error("Chat turn timed out: session=%s after %ss", turn.session_id, CHAT_TURN_TIMEOUT_SECONDS)
    else:
        logger.error("Graph execution error: %s", exc, exc_info=True)
    await _drop_checkpoint(turn.session_id)
    # Issue #9410: never leak exception details in SSE streams.
    queue.put_nowait({"type": "error", "content": TIMEOUT_MESSAGE if timed_out else ERROR_MESSAGE})


async def _run_graph(graph, graph_input, config, queue: asyncio.Queue, turn: _Turn) -> None:
    """Execute the graph under the turn timeout and signal the end via the queue sentinel."""
    try:
        async with asyncio.timeout(CHAT_TURN_TIMEOUT_SECONDS):
            result = await graph.ainvoke(graph_input, config=config)
        # An interrupt (command approval needed) is the turn's last output.
        for intr in result.get("__interrupt__") or []:
            queue.put_nowait(intr.value)
    except asyncio.CancelledError:
        await _finish_cancelled(turn, queue)
        raise
    except Exception as exc:  # noqa: BLE001 - reported to the client, never raised into the consumer
        await _finish_failed(turn, queue, exc)
    finally:
        queue.put_nowait(None)


async def _settle(task: "asyncio.Task[None]") -> None:
    """Await *task*, absorbing only the cancellation we issued to it ourselves."""
    try:
        await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if not task.cancelled() or (current is not None and current.cancelling()):
            raise


async def _stop(turn: _Turn) -> None:
    """Cancel and await the turn's task (the teardown every ending goes through)."""
    task = turn.task
    if task is None:
        return
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _to_message(data: Any) -> Any:
    if hasattr(data, "to_dict"):
        return data
    # Preserve original ID to prevent poll-cycle churn (#1064)
    return WorkflowMessage(
        type=data.get("type", "response"),
        content=data.get("content", ""),
        metadata=data.get("metadata", {}),
        id=data.get("id") or str(uuid.uuid4()),
    )


async def stream_graph_turn(manager, graph, session_id: str, graph_input: Any) -> AsyncIterator[Any]:
    """Run *graph* as a registered turn and yield its messages as they arrive.

    Leaving the iterator for any reason -- normal end, an error, or the client
    closing the stream -- cancels and awaits the underlying task.
    """
    queue: asyncio.Queue = asyncio.Queue()
    config = {
        "configurable": {
            "thread_id": session_id,
            "manager": manager,
            "stream_callback": queue.put_nowait,
        }
    }
    turn = _Turn(session_id=session_id)
    turn.task = asyncio.create_task(_run_graph(graph, graph_input, config, queue, turn))
    _REGISTRY.add(turn)
    try:
        while (data := await queue.get()) is not None:
            yield _to_message(data)
        await _settle(turn.task)
    finally:
        try:
            await _stop(turn)
        finally:
            _REGISTRY.remove(turn)
