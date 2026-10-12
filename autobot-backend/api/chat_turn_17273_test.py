# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""POST /chat/sessions/{id}/turn/cancel: owner-only, idempotent (#17273).

Mounts the real router through a real request, so the ownership dependency is
proven to bind ``session_id`` from the path. Before #17273 the route did not
exist at all (404), so a stop control had nothing to call.
"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from api import chat_turn
from auth_middleware import get_current_user
from chat_workflow import turn_control
from security.session_ownership import validate_session_ownership


def _client(owner: str, seen: list) -> TestClient:
    app = FastAPI()
    app.include_router(chat_turn.router)

    async def _ownership(session_id: str, request: Request = None):  # noqa: ARG001
        seen.append(session_id)
        if session_id != owner:
            raise HTTPException(status_code=403, detail="not your session")
        return {"authorized": True}

    app.dependency_overrides[validate_session_ownership] = _ownership
    app.dependency_overrides[get_current_user] = lambda: {"username": "alice"}
    return TestClient(app, raise_server_exceptions=False)


def test_a_non_owner_is_refused_and_the_turn_survives(monkeypatch):
    cancel = []

    async def _cancel(session_id, reason):
        cancel.append(session_id)
        return 1

    monkeypatch.setattr(chat_turn, "cancel_turn", _cancel)
    seen: list = []
    response = _client("alices-chat", seen).post("/chat/sessions/bobs-chat/turn/cancel")

    assert response.status_code == 403
    assert seen == ["bobs-chat"]
    assert cancel == [], "a refused caller must never reach the teardown"


@pytest.mark.asyncio
async def test_the_owner_cancels_a_running_turn_through_the_real_registry():
    """No mocks below the route: the registered task really is cancelled."""
    state = {}

    async def _turn():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    turn = turn_control._Turn(session_id="alices-chat")
    turn.task = asyncio.create_task(_turn())
    turn_control._REGISTRY.add(turn)
    await asyncio.sleep(0)
    try:
        transport = httpx.ASGITransport(app=_client("alices-chat", []).app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            first = await client.post("/chat/sessions/alices-chat/turn/cancel")
            again = await client.post("/chat/sessions/alices-chat/turn/cancel")
    finally:
        turn_control._REGISTRY.remove(turn)

    assert state["cancelled"] is True and turn.task.cancelled()
    assert first.json()["data"] == {"session_id": "alices-chat", "status": "cancelled", "cancelled_turns": 1}
    assert again.json()["data"]["status"] == "no_active_turn", "a second cancel must be an idempotent no-op"


def test_cancel_reports_cancelled_when_a_turn_was_stopped(monkeypatch):
    async def _cancel(session_id, reason):
        assert reason == turn_control.REASON_CLIENT_CANCEL
        return 1

    monkeypatch.setattr(chat_turn, "cancel_turn", _cancel)
    response = _client("alices-chat", []).post("/chat/sessions/alices-chat/turn/cancel")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "session_id": "alices-chat",
        "status": "cancelled",
        "cancelled_turns": 1,
    }


def test_cancel_with_no_turn_is_a_clear_noop():
    response = _client("alices-chat", []).post("/chat/sessions/alices-chat/turn/cancel")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "no_active_turn"
    assert response.json()["data"]["cancelled_turns"] == 0
