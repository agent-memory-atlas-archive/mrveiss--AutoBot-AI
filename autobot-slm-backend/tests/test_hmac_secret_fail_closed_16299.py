# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""API-key pepper: no published default, transparent legacy migration (#16299).

conftest stubs ``config`` and sibling modules, so the real ``api_key_service``
source is loaded by path against a stand-in ``settings`` and an in-memory
"table" of key hashes.
"""

import asyncio
import hashlib
import hmac
import importlib.util
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_VAR = "SLM_HMAC_API_KEY_SECRET"
_LITERAL = "autobot-api-key-v1"


class _Store:
    """In-memory stand-in for the SystemSecret table plus the System vault."""

    def __init__(self, stored=None):
        self.rows = {}
        if stored is not None:
            self.rows["slm_hmac_api_key_secret"] = stored
        self.mirrored = []

    async def retrieve_secret(self, session, key):
        return self.rows.get(key)

    async def mirror_secret_to_vault(self, key, value):
        self.mirrored.append(key)
        return True


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, store):
        self.store = store

    def begin_nested(self):
        return _Nested()

    def add(self, row):
        self.store.rows[row.key] = row.encrypted_value


def _load(name, path, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, _ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def svc(monkeypatch):
    cfg = SimpleNamespace(hmac_api_key_secret="")
    store = _Store()
    fakes = {
        "config": SimpleNamespace(settings=cfg),
        "models.database": SimpleNamespace(SystemSecret=lambda **kw: SimpleNamespace(**kw)),
        "services.encryption": SimpleNamespace(encrypt_data=lambda v: v),
        "services.system_secrets_vault": SimpleNamespace(
            retrieve_secret=store.retrieve_secret,
            mirror_secret_to_vault=store.mirror_secret_to_vault,
        ),
        "user_management.models.api_key": SimpleNamespace(APIKey=object),
        "user_management.services.base_service": SimpleNamespace(BaseService=object),
    }
    for name, mod in fakes.items():
        m = types.ModuleType(name)
        m.__dict__.update(vars(mod))
        monkeypatch.setitem(sys.modules, name, m)
    pepper = _load("_api_key_pepper_19120", "services/api_key_pepper.py", monkeypatch)
    monkeypatch.setitem(sys.modules, "services.api_key_pepper", pepper)
    mod = _load("_api_key_service_16299", "user_management/services/api_key_service.py", monkeypatch)
    return mod, cfg, store, pepper


def _hm(secret: str, key: str) -> str:
    return hmac.new(secret.encode(), key.encode(), hashlib.sha256).hexdigest()


def _service(mod, rows, store):
    """Service whose lookup is an in-memory dict of hash -> row."""
    s = mod.APIKeyService.__new__(mod.APIKeyService)
    s.session = _FakeSession(store)
    s.session.flush = _noop

    async def find(h):
        return rows.get(h)

    s._find_by_hash = find
    return s


async def _noop():
    return None


def _row():
    return SimpleNamespace(id=1, key_hash="", is_valid=True, record_usage=lambda: None)


def test_config_default_is_empty():
    src = (_ROOT / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("SLM_HMAC_API_KEY_SECRET", "")' in src
    assert _LITERAL not in src


def test_store_value_wins_over_env(svc):
    _, cfg, store, pepper = svc
    store.rows[pepper.HMAC_PEPPER_KEY] = "from-store"  # pragma: allowlist secret
    cfg.hmac_api_key_secret = "from-env"  # pragma: allowlist secret
    assert asyncio.run(pepper.resolve_hmac_api_key_secret(_FakeSession(store))) == "from-store"
    assert store.rows[pepper.HMAC_PEPPER_KEY] == "from-store"
    assert store.mirrored == []


def test_env_only_imports_once_and_never_logs_value(svc, caplog):
    _, cfg, store, pepper = svc
    cfg.hmac_api_key_secret = "env-pepper-value"  # pragma: allowlist secret
    sess = _FakeSession(store)
    with caplog.at_level(logging.DEBUG):
        assert asyncio.run(pepper.resolve_hmac_api_key_secret(sess)) == "env-pepper-value"
        assert store.rows[pepper.HMAC_PEPPER_KEY] == "env-pepper-value"
        assert store.mirrored == [pepper.HMAC_PEPPER_KEY]
        # second call reads the store: no re-import, no second audit line
        assert asyncio.run(pepper.resolve_hmac_api_key_secret(sess)) == "env-pepper-value"
    assert store.mirrored == [pepper.HMAC_PEPPER_KEY]
    assert caplog.text.count("imported SLM_HMAC_API_KEY_SECRET") == 1
    assert "env-pepper-value" not in caplog.text


def test_neither_set_fails_closed_and_warning_names_the_page(svc, caplog):
    mod, _, store, pepper = svc
    s = _service(mod, {}, store)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match=pepper.SECRETS_PAGE_PATH) as exc:
            asyncio.run(s.create_key(None, "n", []))
    assert pepper.HMAC_PEPPER_KEY in str(exc.value) and _LITERAL not in str(exc.value)
    assert pepper.SECRETS_PAGE_PATH in caplog.text and pepper.HMAC_PEPPER_KEY in caplog.text
    assert store.rows == {}


def test_new_hash_never_uses_the_literal(svc):
    mod, *_ = svc
    h = mod.APIKeyService._hash_key("abot_x", "s1")  # pragma: allowlist secret
    assert h == _hm("s1", "abot_x") and h != _hm(_LITERAL, "abot_x")
    with pytest.raises(RuntimeError):
        mod.APIKeyService._hash_key("abot_x", "")


def test_literal_hashed_key_verifies_and_is_rehashed(svc):
    mod, _, store, pepper = svc
    store.rows[pepper.HMAC_PEPPER_KEY] = "s1"  # pragma: allowlist secret
    row = _row()
    row.key_hash = _hm(_LITERAL, "abot_x")
    s = _service(mod, {row.key_hash: row}, store)
    assert asyncio.run(s.validate_key("abot_x")) is row
    assert row.key_hash == _hm("s1", "abot_x")


def test_bare_sha256_key_still_verifies_and_is_rehashed(svc):
    mod, _, store, pepper = svc
    store.rows[pepper.HMAC_PEPPER_KEY] = "s1"  # pragma: allowlist secret
    row = _row()
    row.key_hash = hashlib.sha256(b"abot_x").hexdigest()
    s = _service(mod, {row.key_hash: row}, store)
    assert asyncio.run(s.validate_key("abot_x")) is row
    assert row.key_hash == _hm("s1", "abot_x")


def test_no_pepper_verify_works_warns_and_does_not_rehash(svc, caplog):
    mod, _, store, pepper = svc
    row = _row()
    row.key_hash = _hm(_LITERAL, "abot_x")
    old = row.key_hash
    s = _service(mod, {old: row}, store)
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(s.validate_key("abot_x")) is row
    assert row.key_hash == old
    assert pepper.SECRETS_PAGE_PATH in caplog.text and _LITERAL not in caplog.text


def test_rotation_is_not_supported_old_pepper_hash_stops_verifying(svc):
    """Documented limitation: the store keeps ONE value, so no dual-verify window."""
    mod, _, store, pepper = svc
    store.rows[pepper.HMAC_PEPPER_KEY] = "old"  # pragma: allowlist secret
    row = _row()
    row.key_hash = _hm("old", "abot_x")
    s = _service(mod, {row.key_hash: row}, store)
    store.rows[pepper.HMAC_PEPPER_KEY] = "new"  # pragma: allowlist secret
    assert asyncio.run(s.validate_key("abot_x")) is None


def test_literal_lives_only_in_the_named_constant():
    src = (_ROOT / "user_management/services/api_key_service.py").read_text(encoding="utf-8")
    assert src.count(_LITERAL) == 1
    assert f'_LEGACY_PUBLIC_PEPPER = "{_LITERAL}"' in src
