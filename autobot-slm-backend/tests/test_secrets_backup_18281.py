# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The secrets-store backup is a ciphertext-only SQLite snapshot over the existing SSH path (#18281).

The remote side is faked at ``BackupService._run_command``: every argv the service would send is
recorded, an ``scp`` writes a real file, and ``sha256sum`` answers with that file's real digest. So
the assertions are about what the code SENDS (the online backup, never the key) and what it
RECORDS (status, checksum, completed_at), not about a mock agreeing with itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = b"SQLite format 3\x00 ciphertext rows"


def _load(name: str, rel: str):
    for extra in (str(ROOT), str(ROOT.parent)):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mods(tmp_path, monkeypatch):
    # The harness hands out a MagicMock for models.database, whose BackupStatus.X.value is a
    # truthy mock; the row-state assertions below are only meaningful against the real enum.
    enums = _load("_real_status_enums_18281", "status_enums.py")
    db_module = sys.modules.get("models.database")
    if db_module is not None:
        monkeypatch.setattr(db_module, "BackupStatus", enums.BackupStatus, raising=False)
    try:
        backup = _load("_real_backup_18281", "services/backup.py")
        secrets_backup = _load("_real_secrets_backup_18281", "services/secrets_store_snapshot.py")
    except Exception as exc:
        pytest.skip(f"SLM backup modules not importable here: {exc}")
    monkeypatch.setattr(backup, "BACKUP_STORAGE_DIR", tmp_path)
    return backup, secrets_backup


class _Row:
    def __init__(self, backup_id="bk1"):
        self.backup_id = backup_id
        self.status = "pending"
        self.service_type = "secrets"
        self.backup_path = None
        self.size_bytes = 0
        self.checksum = None
        self.error = None
        self.started_at = None
        self.completed_at = None


class _DB:
    def __init__(self, row):
        self.row = row
        self.commits = 0

    async def execute(self, _q):
        return SimpleNamespace(scalar_one_or_none=lambda: self.row)

    async def commit(self):
        self.commits += 1


def _node(roles):
    return SimpleNamespace(node_id="n1", ip_address="192.0.2.10", ssh_user="autobot", ssh_port=22, roles=roles)


class _Remote:
    """Fake of ``BackupService._run_command``: records argv, serves scp/sha256sum, can fail snapshot."""

    def __init__(self, fail_snapshot=False, corrupt_copy=False):
        self.calls: list[list[str]] = []
        self.fail_snapshot = fail_snapshot
        self.corrupt_copy = corrupt_copy

    async def __call__(self, cmd, timeout=60, stdin=None):
        self.calls.append(list(cmd))
        if cmd[0].endswith("scp"):
            await asyncio.to_thread(Path(cmd[-1]).write_bytes, PAYLOAD + (b"x" if self.corrupt_copy else b""))
            return True, ""
        remote = cmd[-1]
        if "sqlite3" in remote:
            return (False, "boom") if self.fail_snapshot else (True, "")
        if remote.startswith("sha256sum"):
            return True, hashlib.sha256(PAYLOAD).hexdigest() + "\n"
        return True, ""

    def remote_commands(self):
        return [c[-1] for c in self.calls if not c[0].endswith("scp")]


@pytest.fixture()
def service(mods, monkeypatch):
    backup, _ = mods
    svc = backup.BackupService.__new__(backup.BackupService)
    remote = _Remote()
    monkeypatch.setattr(svc, "_run_command", remote)
    svc.remote = remote
    return svc


async def test_success_uses_online_backup_and_never_names_key_material(mods, service):
    _, sb = mods
    row, node = _Row(), _node(["backend"])
    ok, message = await sb.execute_secrets_backup(service, _DB(row), "bk1", node)
    assert ok, message
    remote = "\n".join(service.remote.remote_commands())
    assert ".backup(" in remote and "sqlite3" in remote and "secrets.db" in remote
    assert remote.startswith("umask 077 && ")
    everything = " ".join(" ".join(c) for c in service.remote.calls)
    for forbidden in ("secrets.key", "secrets.json", "AUTOBOT_SECRETS_KEY", "Fernet"):
        assert forbidden not in everything
    scp = next(c for c in service.remote.calls if c[0].endswith("scp"))
    assert "secrets.key" not in " ".join(scp)


async def test_success_records_checksum_status_and_completed_at(mods, service):
    _, sb = mods
    row = _Row()
    ok, _ = await sb.execute_secrets_backup(service, _DB(row), "bk1", _node(["backend"]))
    assert ok
    assert row.status == "completed"
    assert row.checksum == hashlib.sha256(PAYLOAD).hexdigest()
    assert row.completed_at is not None and row.size_bytes == len(PAYLOAD)
    assert await asyncio.to_thread(Path(row.backup_path).read_bytes) == PAYLOAD
    assert Path(row.backup_path).stat().st_mode & 0o077 == 0


async def test_the_staged_snapshot_is_removed_from_the_node(mods, service):
    _, sb = mods
    await sb.execute_secrets_backup(service, _DB(_Row()), "bk1", _node(["backend"]))
    assert service.remote.remote_commands()[-1].startswith("rm -f -- ")


async def test_the_remote_command_quotes_every_interpolated_path(mods, service, monkeypatch):
    _, sb = mods
    monkeypatch.setattr(sb, "BACKEND_DATA_DIR", "/var/lib/odd dir; touch pwned")
    await sb.execute_secrets_backup(service, _DB(_Row()), "bk1", _node(["main"]))
    snapshot = service.remote.remote_commands()[0]
    assert "'/var/lib/odd dir; touch pwned/secrets.db'" in snapshot


@pytest.mark.parametrize("roles", [[], None, ["frontend", "slm"]])
async def test_refuses_without_a_backend_node(mods, service, roles):
    _, sb = mods
    row = _Row()
    ok, message = await sb.execute_secrets_backup(service, _DB(row), "bk1", _node(roles))
    assert not ok and "no registered backend node" in message
    assert row.status == "failed" and "no registered backend node" in row.error
    assert service.remote.calls == []


async def test_a_failed_snapshot_fails_the_row_and_still_cleans_up(mods, service):
    _, sb = mods
    service.remote.fail_snapshot = True
    row = _Row()
    ok, message = await sb.execute_secrets_backup(service, _DB(row), "bk1", _node(["backend"]))
    assert not ok and row.status == "failed" and "snapshot failed" in message
    assert service.remote.remote_commands()[-1].startswith("rm -f -- ")


async def test_a_copy_whose_checksum_differs_is_not_recorded_as_completed(mods, service):
    _, sb = mods
    service.remote.corrupt_copy = True
    row = _Row()
    ok, _ = await sb.execute_secrets_backup(service, _DB(row), "bk1", _node(["backend"]))
    assert not ok and row.status == "failed" and row.checksum is None


async def test_a_missing_row_is_reported_not_raised(mods, service):
    _, sb = mods
    ok, message = await sb.execute_secrets_backup(service, _DB(None), "nope", _node(["backend"]))
    assert (ok, message) == (False, "Backup not found")


def test_the_enum_and_the_dispatch_carry_secrets():
    status_src = (ROOT / "status_enums.py").read_text(encoding="utf-8")
    stateful_src = (ROOT / "api" / "stateful.py").read_text(encoding="utf-8")
    assert "SECRETS = " in status_src and '"secrets"' in status_src
    assert "BackupServiceType.SECRETS: secrets_runner(backup_service)" in stateful_src


def test_the_default_data_dir_is_the_ansible_backend_data_dir(mods):
    _, sb = mods
    defaults = (ROOT / "ansible" / "roles" / "backend" / "defaults" / "main.yml").read_text(encoding="utf-8")
    assert f"backend_data_dir: {sb.BACKEND_DATA_DIR}" in defaults


def test_the_default_service_user_is_the_ansible_backend_user(mods):
    _, sb = mods
    defaults = (ROOT / "ansible" / "roles" / "backend" / "defaults" / "main.yml").read_text(encoding="utf-8")
    assert f"backend_user: {sb.SERVICE_USER}\n" in defaults


def test_the_slm_unit_renders_both_from_the_backend_role_variables():
    unit = (ROOT / "ansible/roles/slm_manager/templates/autobot-slm-backend.service.j2").read_text(encoding="utf-8")
    assert "SLM_BACKEND_DATA_DIR={{ backend_data_dir |" in unit
    assert "SLM_BACKEND_SERVICE_USER={{ backend_user |" in unit


async def test_the_snapshot_runs_as_the_service_user_via_non_interactive_sudo(mods, service, monkeypatch):
    _, sb = mods
    monkeypatch.setattr(sb, "SERVICE_USER", "svc user")
    row, node = _Row(), _node(["backend"])
    assert (await sb.execute_secrets_backup(service, _DB(row), "bk1", node))[0]
    snapshot = service.remote.remote_commands()[0]
    assert "sudo -n -u 'svc user' python3 -c " in snapshot
    assert snapshot.rstrip().endswith("> /tmp/slm-secrets-bk1.db")


async def test_no_sudo_when_the_ssh_user_is_the_service_user(mods, service):
    _, sb = mods
    node = _node(["backend"])
    node.ssh_user = sb.SERVICE_USER
    await sb.execute_secrets_backup(service, _DB(_Row()), "bk1", node)
    assert "sudo" not in service.remote.remote_commands()[0]
