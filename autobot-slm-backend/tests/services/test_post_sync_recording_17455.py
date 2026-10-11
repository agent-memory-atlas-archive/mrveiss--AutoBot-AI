# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Each post-sync command and restart a sync executes leaves a redacted row (#17455)."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
from _real_orm_import import SLM_ROOT, import_modules_with_real_orm  # noqa: E402

service_restart, recorder, runner = import_modules_with_real_orm(
    ("services.post_sync_recorder", "services.post_sync_runner"),
    path_loaded={
        "services.service_restart": SLM_ROOT / "services" / "service_restart.py",
    },
)

_REC = recorder.RunContext("run1", "n1", "autobot-backend")
_NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def _outcome(**kw):
    base = dict(success=True, exit_code=0, output_tail="", error=None, started_at=_NOW, finished_at=_NOW)
    return recorder.ActionOutcome(**{**base, **kw})


def test_row_content_is_redacted_and_complete():
    out = _outcome(success=False, exit_code=1, output_tail="x" * 900 + " api_key=hunter2", error="token=abc123")
    row = recorder.build_row(
        _REC, "post_sync", "Post-sync command", "curl -H 'Authorization: Bearer s3cr3t'", None, out
    )
    assert (row.run_id, row.node_id, row.role_name, row.category) == ("run1", "n1", "autobot-backend", "post_sync")
    assert (row.exit_code, row.success, row.started_at, row.finished_at) == (1, False, _NOW, _NOW)
    assert "s3cr3t" not in row.command
    assert "hunter2" not in row.output_tail and len(row.output_tail) <= 800
    assert "abc123" not in row.error


async def test_recording_never_fails_the_sync(monkeypatch):
    broken = MagicMock()
    broken.session.side_effect = RuntimeError("db down")
    monkeypatch.setattr(recorder, "db_service", broken)
    await recorder.record_action_run(_REC, "restart", "Restart a", "cmd", "a", _outcome())  # must not raise


class _Proc:
    def __init__(self, rc, out):
        self.returncode, self._out = rc, out

    async def communicate(self):
        return self._out, b""


def _fake_ssh(monkeypatch, rc=0, out=b""):
    async def _spawn(*argv, **kwargs):
        return _Proc(rc, out)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)


@pytest.fixture
def recorded(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(runner, "record_action_run", mock)
    return mock


def _base():
    return runner.restart_ssh_base("node.example.test", "svc", 22)


async def test_a_failed_restart_is_recorded(monkeypatch, recorded):
    _fake_ssh(monkeypatch, rc=1, out=b"Failed to restart a.service")
    outcome = await runner.restart_units(_base(), ["a.service"], _REC)
    (rec, category, label, command, units, recorded_outcome), _ = recorded.call_args
    assert (category, label, units) == ("restart", "Restart a.service", "a.service")
    assert command == "sudo -n systemctl restart -- a.service"
    assert (recorded_outcome.success, recorded_outcome.exit_code) == (False, 1)
    assert outcome.success is False


async def test_a_failed_post_sync_command_is_recorded_and_reported(monkeypatch, recorded):
    _fake_ssh(monkeypatch, rc=2, out=b"pip: no matching distribution")
    outcome = await runner.run_post_sync_command(_base(), "pip install x", 5, _REC, "Install deps", "install")
    assert (outcome.success, outcome.exit_code) == (False, 2)
    assert "no matching distribution" in outcome.output_tail
    args = recorded.call_args.args
    assert (args[1], args[2], args[3]) == ("install", "Install deps", "pip install x")


async def test_without_a_run_context_nothing_is_recorded(monkeypatch, recorded):
    _fake_ssh(monkeypatch)
    assert (await runner.restart_units(_base(), ["a.service"])).success
    recorded.assert_not_called()
