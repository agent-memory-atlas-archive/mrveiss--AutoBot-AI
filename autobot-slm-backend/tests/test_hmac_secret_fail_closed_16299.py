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


def _config_require(self):
    # lifted verbatim from config.py so the real method is what runs
    import ast

    tree = ast.parse((_ROOT / "config.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if getattr(n, "name", "") == "require_hmac_api_key_secret")
    ns: dict = {}
    exec(compile(ast.fix_missing_locations(ast.Module([fn], [])), "config.py", "exec"), ns)  # nosec B102
    return ns["require_hmac_api_key_secret"](self)


@pytest.fixture
def svc(monkeypatch):
    cfg = SimpleNamespace(hmac_api_key_secret="")
    cfg.require_hmac_api_key_secret = lambda: _config_require(cfg)
    fakes = {
        "config": SimpleNamespace(settings=cfg),
        "user_management.models.api_key": SimpleNamespace(APIKey=object),
        "user_management.services.base_service": SimpleNamespace(BaseService=object),
    }
    for name, mod in fakes.items():
        m = types.ModuleType(name)
        m.__dict__.update(vars(mod))
        monkeypatch.setitem(sys.modules, name, m)
    spec = importlib.util.spec_from_file_location(
        "_api_key_service_16299", _ROOT / "user_management/services/api_key_service.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, cfg


def _hm(secret: str, key: str) -> str:
    return hmac.new(secret.encode(), key.encode(), hashlib.sha256).hexdigest()


def _service(mod, rows):
    """Service whose lookup is an in-memory dict of hash -> row."""
    s = mod.APIKeyService.__new__(mod.APIKeyService)
    s.session = SimpleNamespace(flush=_noop)

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


def test_new_key_fails_closed_naming_variable(svc):
    mod, _ = svc
    with pytest.raises(RuntimeError, match=_VAR) as exc:
        mod.APIKeyService._hash_key("abot_x")
    assert _LITERAL not in str(exc.value)


def test_new_hash_never_uses_the_literal(svc):
    mod, cfg = svc
    cfg.hmac_api_key_secret = "s1"  # pragma: allowlist secret
    assert mod.APIKeyService._hash_key("abot_x") == _hm("s1", "abot_x")
    assert mod.APIKeyService._hash_key("abot_x") != _hm(_LITERAL, "abot_x")


def test_literal_hashed_key_verifies_and_is_rehashed(svc):
    mod, cfg = svc
    cfg.hmac_api_key_secret = "s1"  # pragma: allowlist secret
    row = _row()
    row.key_hash = _hm(_LITERAL, "abot_x")
    s = _service(mod, {row.key_hash: row})
    assert asyncio.run(s.validate_key("abot_x")) is row
    assert row.key_hash == _hm("s1", "abot_x")


def test_bare_sha256_key_still_verifies_and_is_rehashed(svc):
    mod, cfg = svc
    cfg.hmac_api_key_secret = "s1"  # pragma: allowlist secret
    row = _row()
    row.key_hash = hashlib.sha256(b"abot_x").hexdigest()
    s = _service(mod, {row.key_hash: row})
    assert asyncio.run(s.validate_key("abot_x")) is row
    assert row.key_hash == _hm("s1", "abot_x")


def test_no_secret_verify_works_warns_and_does_not_rehash(svc, caplog):
    mod, _ = svc
    row = _row()
    row.key_hash = _hm(_LITERAL, "abot_x")
    old = row.key_hash
    s = _service(mod, {old: row})
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(s.validate_key("abot_x")) is row
    assert row.key_hash == old
    assert _VAR in caplog.text and _LITERAL not in caplog.text


def test_literal_lives_only_in_the_named_constant():
    src = (_ROOT / "user_management/services/api_key_service.py").read_text(encoding="utf-8")
    assert src.count(_LITERAL) == 1
    assert f'_LEGACY_PUBLIC_PEPPER = "{_LITERAL}"' in src
