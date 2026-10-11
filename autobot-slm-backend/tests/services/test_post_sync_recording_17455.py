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

service_restart, recorder, runner, sync_orchestrator = import_modules_with_real_orm(
    ("services.post_sync_recorder", "services.post_sync_runner", "services.sync_orchestrator"),
    path_loaded={"services.service_restart": SLM_ROOT / "services" / "service_restart.py"},
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


def _ctx(cmd=None, units=("a.service",)):
    ctx = sync_orchestrator.SyncNodeContext()
    ctx.node_ip, ctx.auto_restart, ctx.systemd_service, ctx.post_sync_cmd = "node.example.test", True, list(units), cmd
    return ctx


async def test_a_failed_restart_is_recorded(monkeypatch, recorded):
    _fake_ssh(monkeypatch, rc=1, out=b"Failed to restart a.service")
    await sync_orchestrator.SyncOrchestrator()._restart_systemd_service(_ctx(), "n1", True, _REC)
    (rec, category, label, command, units, outcome), _ = recorded.call_args
    assert (category, label, units) == ("restart", "Restart a.service", "a.service")
    assert command == "sudo -n systemctl restart -- a.service"
    assert (outcome.success, outcome.exit_code) == (False, 1)


async def test_a_failed_post_sync_command_is_recorded_and_reported(monkeypatch, recorded):
    _fake_ssh(monkeypatch, rc=2, out=b"pip: no matching distribution")
    outcome = await sync_orchestrator.SyncOrchestrator()._run_post_sync_command(_ctx("pip install x"), _REC)
    assert (outcome.success, outcome.exit_code) == (False, 2)
    assert "no matching distribution" in outcome.output_tail
    args = recorded.call_args.args
    assert (args[1], args[3]) == ("post_sync", "pip install x")


async def test_no_post_sync_command_records_nothing(monkeypatch, recorded):
    outcome = await sync_orchestrator.SyncOrchestrator()._run_post_sync_command(_ctx(None), _REC)
    assert outcome.success
    recorded.assert_not_called()


@pytest.mark.parametrize(("given", "expect_default"), [(None, True), ("fixedrun", False)])
async def test_sync_node_role_run_id_is_optional_and_defaults_to_uuid4_hex(
    tmp_path, monkeypatch, given, expect_default
):
    orch = sync_orchestrator.SyncOrchestrator.__new__(sync_orchestrator.SyncOrchestrator)
    orch.cache_dir = tmp_path
    (tmp_path / "c1").mkdir()
    seen = {}

    async def _post(ctx, rec=None):
        seen["rec"] = rec
        return _outcome()

    ctx = _ctx()
    ctx.source_paths = []
    monkeypatch.setattr(orch, "_get_node_and_role_info", AsyncMock(return_value=(True, "OK", ctx)))
    monkeypatch.setattr(orch, "_run_post_sync_command", _post)
    monkeypatch.setattr(orch, "_restart_systemd_service", AsyncMock())
    monkeypatch.setattr(orch, "_update_node_role_record", AsyncMock())
    ok, _ = await orch.sync_node_role("n1", "autobot-backend", "c1", True, **({"run_id": given} if given else {}))
    assert ok
    run_id = seen["rec"].run_id
    assert (len(run_id) == 32 and int(run_id, 16) >= 0) if expect_default else run_id == "fixedrun"
