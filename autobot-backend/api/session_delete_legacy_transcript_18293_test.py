# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A session delete never removes a legacy transcript its caller does not durably own (#18293).

``delete_session`` used to run every cleanup BEFORE deleting the session, and
``validate_session_ownership`` hands an ownerless session to whoever asks
(``reason="legacy_migration"``). Once the transcript cleanup pointed at the real
directory, any user could delete another user's legacy transcript by session id.

These tests drive the real ``delete_session`` body (unwrapped from its error
decorator) with the side services stubbed, and assert on the transcript FILE.
They are written to run against the old ordering too: there the "kept" cases fail
because the file is gone -- the contrast the fix is measured against.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import api.chat_sessions as chat_sessions
from autobot_shared.ssot_config import config
from security.enforcement_mode import ResolvedEnforcementMode
from security.session_ownership import SessionOwnershipValidator

try:  # absent on the pre-fix tree, which this file is also run against as the contrast
    import api.legacy_transcript_disposal as disposal
except ImportError:  # pragma: no cover - contrast run only
    disposal = None

ALICE, BOB = "alice", "bob"
SESSION, OTHER = "sess-alice", "sess-other"


class _ChatHistory:
    """Sessions in chat_sessions, each with the owner recorded on its file (or None)."""

    def __init__(self, owners: dict) -> None:
        self.owners = dict(owners)

    async def get_session_message_count(self, session_id: str) -> int:
        return 0

    async def get_session_owner(self, session_id: str):
        return self.owners.get(session_id)

    async def delete_session(self, session_id: str) -> bool:
        return self.owners.pop(session_id, "absent") != "absent"


@pytest.fixture
def legacy_dir(tmp_path, monkeypatch) -> Path:
    """One transcript directory, reachable by both the SSOT setting and the old cwd resolution."""
    monkeypatch.chdir(tmp_path)
    directory = (tmp_path / "data" / "conversation_transcripts").resolve()
    directory.mkdir(parents=True)
    for session_id in (SESSION, OTHER):
        (directory / f"{session_id}.json").write_text('{"messages": []}', encoding="utf-8")
    if hasattr(config.path, "legacy_transcripts_dir"):  # absent on the pre-fix (cwd-resolving) tree
        monkeypatch.setattr(config.path, "legacy_transcripts_dir", str(directory))
    return directory


@pytest.fixture
def proposals(monkeypatch) -> list:
    recorded: list = []

    async def _propose(session_id, requested_by, why):
        recorded.append((session_id, requested_by))
        return f"approval-{len(recorded)}"

    if disposal is not None:
        monkeypatch.setattr(disposal, "propose_transcript_disposal", _propose)
    return recorded


def _stub_side_services(monkeypatch, chat_history: _ChatHistory) -> None:
    for name in ("_handle_conversation_files", "_cleanup_terminal_sessions"):
        monkeypatch.setattr(chat_sessions, name, AsyncMock(return_value={}))
    for name in ("_cleanup_knowledge_base_facts", "_cleanup_chat_knowledge_context"):
        monkeypatch.setattr(chat_sessions, name, AsyncMock(return_value={}))
    monkeypatch.setattr(chat_sessions, "get_chat_history_manager", lambda _request: chat_history)
    for name in ("_emit_session_destroy", "publish_session_deleted"):
        monkeypatch.setattr(chat_sessions, name, AsyncMock())
    monkeypatch.setattr(chat_sessions, "log_chat_event", MagicMock())
    monkeypatch.setattr(chat_sessions, "audit_record", MagicMock())
    auth = SimpleNamespace(get_user_from_request=lambda _r: {"user_id": "u"})
    monkeypatch.setattr(chat_sessions, "get_auth_middleware", lambda: auth)


async def _delete(session_id: str, username: str, reason: str):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), client=None, headers={}, url="")
    ownership = {"authorized": True, "reason": reason, "user_data": {"username": username}}
    body = chat_sessions.delete_session.__wrapped__
    return await body(session_id, request, ownership=ownership, file_action="delete", file_options=None)


@pytest.fixture(autouse=True)
def _quiet_request_logging(monkeypatch):
    monkeypatch.setattr(chat_sessions, "log_request_context", MagicMock())


@pytest.mark.asyncio
async def test_another_users_owned_session_is_denied_and_its_transcript_untouched(legacy_dir) -> None:
    validator = SessionOwnershipValidator.__new__(SessionOwnershipValidator)
    validator.redis = MagicMock()
    validator.get_session_owner = AsyncMock(return_value=ALICE)
    validator._check_ownership_mismatch = AsyncMock(return_value={"authorized": False, "reason": "mismatch"})
    validator._audit_log_success = MagicMock()
    validator.set_session_owner = AsyncMock(return_value=True)

    result = await validator._resolve_ownership(
        session_id=SESSION,
        username=BOB,
        user_data={"username": BOB},
        request=MagicMock(),
        enforcement=ResolvedEnforcementMode("enforced"),
    )

    assert result["authorized"] is False
    validator._check_ownership_mismatch.assert_awaited()  # routed to mismatch, never claimed
    validator.set_session_owner.assert_not_awaited()
    assert (legacy_dir / f"{SESSION}.json").exists()


@pytest.mark.asyncio
async def test_a_claimer_of_a_legacy_only_session_gets_a_proposal_and_a_404(legacy_dir, proposals, monkeypatch) -> None:
    _stub_side_services(monkeypatch, _ChatHistory({}))

    with pytest.raises(Exception, match="not found") as raised:
        await _delete(SESSION, BOB, "legacy_migration")

    assert "approval-1" in str(raised.value)
    assert proposals == [(SESSION, BOB)]
    assert (legacy_dir / f"{SESSION}.json").exists() and (legacy_dir / f"{OTHER}.json").exists()


@pytest.mark.parametrize(
    ("reason", "durable_owner", "caller"),
    [
        ("legacy_migration", None, BOB),  # an ownerless chat session claimed by this very request
        ("owner_match", None, BOB),  # claimed earlier: Redis says bob, the session file names nobody
        ("log_only_mode", ALICE, BOB),  # mismatch let through by a log-only enforcement mode
        ("org_admin", ALICE, BOB),  # a grant, not ownership
    ],
)
@pytest.mark.asyncio
async def test_a_delete_without_durable_ownership_keeps_the_transcript(
    legacy_dir, proposals, monkeypatch, reason, durable_owner, caller
) -> None:
    _stub_side_services(monkeypatch, _ChatHistory({SESSION: durable_owner}))

    response = await _delete(SESSION, caller, reason)

    assert (legacy_dir / f"{SESSION}.json").exists(), reason
    assert proposals == [(SESSION, caller)]
    assert response is not None


@pytest.mark.asyncio
async def test_the_owner_deleting_their_own_session_removes_only_their_file(legacy_dir, proposals, monkeypatch) -> None:
    _stub_side_services(monkeypatch, _ChatHistory({SESSION: ALICE, OTHER: BOB}))

    await _delete(SESSION, ALICE, "owner_match")

    assert not (legacy_dir / f"{SESSION}.json").exists()
    assert (legacy_dir / f"{OTHER}.json").exists()
    assert proposals == []


@pytest.mark.asyncio
async def test_nothing_is_cleaned_up_when_the_session_does_not_exist(legacy_dir, proposals, monkeypatch) -> None:
    chat_history = _ChatHistory({})
    _stub_side_services(monkeypatch, chat_history)

    with pytest.raises(Exception, match="not found"):
        await _delete(OTHER, ALICE, "legacy_migration")

    chat_sessions._cleanup_terminal_sessions.assert_not_awaited()
    chat_sessions._handle_conversation_files.assert_not_awaited()
