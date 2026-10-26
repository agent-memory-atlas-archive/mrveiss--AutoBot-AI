# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""The embedding-model switch endpoints: start (admin), status, cancel (admin) (#19379)."""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from api import embeddings
from api.schemas_embeddings import EmbeddingSwitchRequest
from knowledge import embedding_switch

pytestmark = pytest.mark.asyncio

_STATUS = {"state": "building", "model": "bge-m3:latest", "collection": "kb_bge", "done": 3, "total": 9}


@pytest.fixture
def switch(monkeypatch):
    calls = []

    async def _start(kb, model):
        calls.append(("start", kb, model))
        if model == "phi3:mini":
            raise embedding_switch.SwitchRefused("'phi3:mini' is not an embedding model")
        if model == "busy":
            raise embedding_switch.SwitchRefused("a switch is already running")
        return dict(_STATUS, failed_ids=[], extra="ignored")

    async def _status():
        return dict(_STATUS, failed_ids=[])

    async def _kb(request):
        return "the-kb"

    import dependencies

    monkeypatch.setattr(embedding_switch, "start_switch", _start)
    monkeypatch.setattr(embedding_switch, "switch_status", _status)
    monkeypatch.setattr(dependencies, "provide_knowledge_base", _kb)
    return calls


async def test_start_runs_the_switch_against_the_app_kb(switch):
    result = await embeddings.start_embedding_switch(EmbeddingSwitchRequest(model="bge-m3:latest"), request=None)
    assert switch == [("start", "the-kb", "bge-m3:latest")]
    assert result.data.state == "building" and (result.data.done, result.data.total) == (3, 9)


@pytest.mark.parametrize("model, code", [("phi3:mini", 400), ("busy", 409)])
async def test_a_refused_start_is_a_client_error(switch, model, code):
    with pytest.raises(HTTPException) as exc:
        await embeddings.start_embedding_switch(EmbeddingSwitchRequest(model=model), request=None)
    assert exc.value.status_code == code


async def test_status_reports_progress(switch):
    result = await embeddings.get_embedding_switch_status()
    assert result.data.model == "bge-m3:latest" and result.data.failed_ids == []


async def test_cancel_without_a_running_switch_is_409(switch, monkeypatch):
    async def _no():
        return False

    monkeypatch.setattr(embedding_switch, "request_switch_cancel", _no)
    with pytest.raises(HTTPException) as exc:
        await embeddings.cancel_embedding_switch()
    assert exc.value.status_code == 409


def test_start_and_cancel_require_admin_status_does_not():
    """Owner decision 2026-10-25: only admins start or cancel a switch."""
    for handler in (embeddings.start_embedding_switch, embeddings.cancel_embedding_switch):
        assert "check_admin_permission" in inspect.getsource(handler)
    assert "get_current_user" in inspect.getsource(embeddings.get_embedding_switch_status)
