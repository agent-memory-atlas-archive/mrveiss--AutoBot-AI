# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Every path that changes the SLM admin password revokes the tokens issued before it (#18672).

Startup's SLM_ADMIN_PASSWORD re-sync and `autobot-admin.py reset-password` changed the hash
without recording a password epoch, so an older token stayed valid until it expired. Each test
issues a token, changes the password through the path, and asks the real revocation rule.
"""

import asyncio
import importlib.util
import secrets
import sys
import time
import types
from pathlib import Path

import pytest

from autobot_shared.auth.jwt_core import hash_password
from autobot_shared.user_management import password_epoch

_ADMIN_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "autobot-admin.py"


def _throwaway() -> str:
    """A fresh random value per test -- nothing credential-like is written in this file."""
    return secrets.token_urlsafe(12)


@pytest.fixture()
def epochs():
    """An in-memory epoch store routed through the real override hook."""
    store: dict[str, int] = {}

    async def get_epoch(subject):
        return store.get(subject)

    async def set_epoch(subject, *, now=None):
        store[subject] = int(time.time()) if now is None else now
        return store[subject]

    password_epoch.use_epoch_store(get_epoch=get_epoch, set_epoch=set_epoch)
    yield store
    password_epoch.reset_epoch_store()


def _revoked(subject: str, issued_at: int) -> bool:
    return asyncio.run(password_epoch.is_token_revoked_by_password_change({"sub": subject, "iat": issued_at}))


class _Db:
    async def flush(self):
        return None


def test_startup_sync_revokes_older_tokens_when_the_password_changed(epochs) -> None:
    from services.admin_password_sync import sync_admin_password

    user = types.SimpleNamespace(username="admin", password_hash=hash_password(_throwaway()))
    issued = int(time.time()) - 5
    assert not _revoked("admin", issued)
    assert asyncio.run(sync_admin_password(_Db(), user, _throwaway())) is True
    assert _revoked("admin", issued), "a token issued before the re-sync still works"


def test_startup_sync_records_nothing_when_the_password_is_unchanged(epochs) -> None:
    from services.admin_password_sync import sync_admin_password

    value = _throwaway()
    user = types.SimpleNamespace(username="admin", password_hash=hash_password(value))
    assert asyncio.run(sync_admin_password(_Db(), user, value)) is False
    assert "admin" not in epochs, "every restart would revoke every admin session"


class _Cursor:
    def __init__(self, log):
        self.log, self.rowcount = log, 1

    def execute(self, sql, params):
        self.log.append(("execute", sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return _Cursor(self.log)

    def commit(self):
        self.log.append(("commit",))

    def close(self):
        self.log.append(("close",))


def _admin_module(monkeypatch, log):
    monkeypatch.setitem(sys.modules, "psycopg2", types.SimpleNamespace(connect=lambda url: _Conn(log)))
    monkeypatch.setitem(
        sys.modules, "bcrypt", types.SimpleNamespace(hashpw=lambda pw, salt: b"hashed", gensalt=lambda rounds: b"s")
    )
    spec = importlib.util.spec_from_file_location("autobot_admin_18672", _ADMIN_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "get_db_url", lambda: "postgresql://slm-db/slm")
    return module


def test_cli_reset_records_the_epoch_in_the_same_transaction(monkeypatch, epochs) -> None:
    log: list = []
    module = _admin_module(monkeypatch, log)
    issued = int(time.time()) - 5
    # A non-admin user: the reset path is the same, and it skips the admin-only secrets-file sync.
    module.cmd_reset_password(types.SimpleNamespace(username="operator", new_password=_throwaway()))
    executes = [entry for entry in log if entry[0] == "execute"]
    assert [e[1].split()[0] for e in executes[:2]] == ["UPDATE", "INSERT"], executes
    assert "slm_password_epochs" in executes[1][1]
    assert log.index(executes[1]) < log.index(("commit",)), "the epoch must commit with the hash"
    subject, epoch = executes[1][2]
    assert subject == "operator" and epoch >= issued
    epochs[subject] = epoch  # what the upsert wrote, as the SLM's store would read it back
    assert _revoked("operator", issued), "a token issued before the CLI reset still works"
