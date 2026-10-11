# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Every reader of the legacy transcripts resolves the directory the writer used (#18293).

The retired writer put ``data/conversation_transcripts`` under the process working
directory -- the backend's code directory in the service. The data-storage view and
session-delete cleanup looked under the project root's ``data/`` instead, so they never
saw a real transcript and a deleted session's file stayed on disk. Each test runs from a
``tmp_path`` working directory, with the old project-root location as the contrast.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import api.data_storage as data_storage
from api.chat_sessions_delete_cleanup import _cleanup_conversation_transcript
from api.data_storage_categories import category_dir, get_cleanable_categories, get_protected_categories
from autobot_shared.ssot_constants import PATH
from autobot_shared.store_authority import LEGACY_CONVERSATION_TRANSCRIPTS, legacy_conversation_transcripts_dir
from chat_history.legacy_transcripts import read_legacy_transcript_pairs
from chat_history.session import SessionMixin

SESSION = "sess-18293"
_PAYLOAD = '{"messages": [{"user": "q", "assistant": "a"}]}'


def _write(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{SESSION}.json"
    path.write_text(_PAYLOAD, encoding="utf-8")
    return path


def _write_named(directory: Path, session_id: str) -> Path:
    path = directory / f"{session_id}.json"
    path.write_text(_PAYLOAD, encoding="utf-8")
    return path


@pytest.fixture
def writer_dir(tmp_path, monkeypatch) -> Path:
    """The backend's working directory, and the transcript directory the writer used under it."""
    monkeypatch.chdir(tmp_path)
    return (tmp_path / "data" / LEGACY_CONVERSATION_TRANSCRIPTS).resolve()


def test_every_reader_resolves_the_writers_directory(writer_dir) -> None:
    assert legacy_conversation_transcripts_dir() == writer_dir
    assert category_dir(data_storage.DATA_DIR, LEGACY_CONVERSATION_TRANSCRIPTS) == writer_dir
    assert data_storage._safe_data_path(LEGACY_CONVERSATION_TRANSCRIPTS) == writer_dir


def test_the_old_project_root_resolution_is_a_different_directory(writer_dir) -> None:
    """Contrast: what data_storage and delete-cleanup used to join, before #18293."""
    assert (data_storage.DATA_DIR / LEGACY_CONVERSATION_TRANSCRIPTS).resolve() != writer_dir
    assert (PATH.DATA_DIR / LEGACY_CONVERSATION_TRANSCRIPTS).resolve() != writer_dir


@pytest.mark.asyncio
async def test_the_legacy_reader_finds_the_writers_file(writer_dir) -> None:
    _write(writer_dir)
    assert await read_legacy_transcript_pairs(SESSION) == [{"user": "q", "assistant": "a"}]
    assert await read_legacy_transcript_pairs("../escape") == []


@pytest.mark.asyncio
async def test_deleting_a_session_removes_its_own_transcript(writer_dir) -> None:
    own = _write(writer_dir)
    other = _write_named(writer_dir, "someone-else")

    result = await _cleanup_conversation_transcript(SESSION)

    assert result == {"transcript_deleted": True, "error": None}
    assert not own.exists() and other.exists()


def test_the_legacy_category_is_protected_from_bulk_cleanup() -> None:
    protected = {c["path"] for c in get_protected_categories()}
    cleanable = {c["path"] for c in get_cleanable_categories()}
    assert LEGACY_CONVERSATION_TRANSCRIPTS in protected and LEGACY_CONVERSATION_TRANSCRIPTS not in cleanable


@pytest.mark.asyncio
async def test_session_delete_clears_the_llm_context_cache_too() -> None:
    mixin = SessionMixin.__new__(SessionMixin)
    mixin.redis_client = MagicMock()

    await mixin._clear_session_redis_cache(SESSION)

    mixin.redis_client.delete.assert_called_once_with(f"chat:session:{SESSION}", f"chat:conversation:{SESSION}")
