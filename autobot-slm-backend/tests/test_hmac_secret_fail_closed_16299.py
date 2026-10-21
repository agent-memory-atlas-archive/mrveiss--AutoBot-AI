# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""An unset SLM_HMAC_API_KEY_SECRET fails closed, naming the variable (#16299).

conftest stubs ``config`` and the service modules in ``sys.modules``, so the two
functions under test are lifted out of their real source files by AST and run
against a stand-in ``settings`` -- the real code, not a mock of it.
"""

import ast
import hashlib
import hmac
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_VAR = "SLM_HMAC_API_KEY_SECRET"


def _lift(rel: str, name: str, env: dict):
    tree = ast.parse((_ROOT / rel).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    fn.decorator_list = []
    mod = ast.Module(body=[fn], type_ignores=[])
    ns = {"hmac": hmac, "hashlib": hashlib, **env}
    exec(compile(ast.fix_missing_locations(mod), rel, "exec"), ns)  # noqa: S102  # nosec B102
    return ns[name]


def _hash_key(secret: str):
    cfg = SimpleNamespace(hmac_api_key_secret=secret)
    cfg.require_hmac_api_key_secret = lambda: _lift("config.py", "require_hmac_api_key_secret", {})(cfg)
    return _lift("user_management/services/api_key_service.py", "_hash_key", {"settings": cfg})


def test_config_default_is_empty():
    src = (_ROOT / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("SLM_HMAC_API_KEY_SECRET", "")' in src
    assert "autobot-api-key-v1" not in src


def test_unset_secret_raises_naming_variable():
    with pytest.raises(RuntimeError, match=_VAR) as exc:
        _hash_key("")("abot_x")
    assert "autobot-api-key-v1" not in str(exc.value)


def test_configured_secret_is_used():
    assert _hash_key("s1")("abot_x") != _hash_key("s2")("abot_x")
    expected = hmac.new(b"s1", b"abot_x", hashlib.sha256).hexdigest()
    assert _hash_key("s1")("abot_x") == expected
