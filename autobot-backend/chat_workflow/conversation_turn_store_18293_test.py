# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""LLM context is a projection of chat_sessions; the transcript store is read-only (#18293).

Every test runs from a ``tmp_path`` working directory, which is where the retired
writer resolved its relative ``data/conversation_transcripts`` path -- so "no file
was written" is checked against the exact place the old code wrote to.
"""

import json
import logging
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

import chat_history
import chat_history.legacy_transcripts as legacy
from chat_history.legacy_transcripts import LEGACY_TRANSCRIPT_DIR
from chat_workflow.conversation import ConversationHandlerMixin
from chat_workflow.models import WorkflowSession

SESSION = "sess-18293"


class _Handler(ConversationHandlerMixin):
    def __init__(self, redis_client=None) -> None:
        self.redis_client = redis_client
        self.conversation_history_ttl = 60


def _fake_chat_history(monkeypatch, messages: List[Dict[str, Any]] | Exception) -> None:
    class _Fake:
        async def load_session(self, session_id: str):
            if isinstance(messages, Exception):
                raise messages
            return list(messages)

    monkeypatch.setattr(chat_history, "ChatHistoryManager", _Fake)


def _write_legacy(root, exchanges: List[Dict[str, str]]):
    directory = root / LEGACY_TRANSCRIPT_DIR
    directory.mkdir(parents=True)
    path = directory / f"{SESSION}.json"
    path.write_text(json.dumps({"session_id": SESSION, "messages": exchanges}), encoding="utf-8")
    return path


def _bytes(path) -> bytes:
    return path.read_bytes()


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(legacy, "_LOGGED_SESSIONS", set())


@pytest.mark.asyncio
async def test_context_is_projected_from_chat_sessions(monkeypatch, tmp_path) -> None:
    _write_legacy(tmp_path, [{"user": "old", "assistant": "stale"}])
    _fake_chat_history(
        monkeypatch,
        [{"sender": "user", "text": "hi"}, {"sender": "assistant", "text": "hello", "messageType": "response"}],
    )

    assert await _Handler()._load_conversation_history(SESSION) == [{"user": "hi", "assistant": "hello"}]


@pytest.mark.asyncio
async def test_a_session_with_turns_only_in_a_legacy_file_stays_readable(monkeypatch, tmp_path, caplog) -> None:
    path = _write_legacy(tmp_path, [{"user": "old q", "assistant": "old a"}])
    before = _bytes(path)
    _fake_chat_history(monkeypatch, [])
    handler = _Handler()

    with caplog.at_level(logging.INFO):
        first = await handler._load_conversation_history(SESSION)
        second = await handler._load_conversation_history(SESSION)

    assert first == second == [{"user": "old q", "assistant": "old a"}]
    assert _bytes(path) == before
    assert sum("legacy transcript file" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.asyncio
async def test_an_unreadable_chat_session_does_not_fall_back_to_the_legacy_file(monkeypatch, tmp_path) -> None:
    _write_legacy(tmp_path, [{"user": "old q", "assistant": "old a"}])
    _fake_chat_history(monkeypatch, PermissionError("denied"))

    assert await _Handler()._load_conversation_history(SESSION) == []


@pytest.mark.asyncio
async def test_a_warm_cache_is_served_without_reading_chat_sessions(monkeypatch) -> None:
    redis = AsyncMock()
    redis.get.return_value = json.dumps([{"user": "c", "assistant": "d"}])
    _fake_chat_history(monkeypatch, AssertionError("chat_sessions must not be read on a cache hit"))

    assert await _Handler(redis)._load_conversation_history(SESSION) == [{"user": "c", "assistant": "d"}]


@pytest.mark.asyncio
async def test_legacy_context_is_never_written_into_the_cache(monkeypatch, tmp_path) -> None:
    _write_legacy(tmp_path, [{"user": "old q", "assistant": "old a"}])
    _fake_chat_history(monkeypatch, [])
    redis = AsyncMock()
    redis.get.return_value = None

    await _Handler(redis)._load_conversation_history(SESSION)

    redis.set.assert_not_called()


@pytest.mark.asyncio
async def test_persisting_a_turn_writes_no_transcript_file(tmp_path) -> None:
    redis = AsyncMock()
    session = WorkflowSession(session_id=SESSION, workflow=None, conversation_history=[])

    await _Handler(redis)._persist_conversation(SESSION, session, "q", "a")

    assert not (tmp_path / "data").exists()
    assert session.conversation_history == [{"user": "q", "assistant": "a"}]
    key, value = redis.set.call_args.args
    assert key == f"chat:conversation:{SESSION}" and json.loads(value) == [{"user": "q", "assistant": "a"}]


def test_the_transcript_writer_is_gone() -> None:
    for name in ("_append_to_transcript", "_write_transcript_atomic", "_get_transcript_path", "_load_transcript"):
        assert not hasattr(ConversationHandlerMixin, name), name
