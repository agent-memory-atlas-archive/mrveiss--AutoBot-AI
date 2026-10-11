# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Both services answer the same (role, scopes, DB grants, permission) table identically (#18263, #18264).

Each service's REAL ``require_permission`` (the backend's ``auth_rbac``, the SLM's
``services.auth``) is imported in its own subprocess, because the two services
reuse top-level package names (``services``, ``user_management``, ``config``) and
cannot share an interpreter. The probe drives every row through the dependency,
with the deny-audit writer and the DB-grant loader stood in for, and reports one
verdict per row plus how many denials were audited.

The assertions: identical verdicts row for row; every 403 audited exactly once in
both services; and a non-trivial mix, so a probe that answered 401 to everything
could not pass as parity.
"""

import json
import os
import pathlib
import subprocess  # nosec B404  # runs this repo's own probe under the test interpreter
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]

_PROBE = r"""
import asyncio, json, sys, types
from autobot_shared.auth import permission_gate
from autobot_shared.auth.permissions import Permission, Role

module = __import__(sys.argv[1], fromlist=["require_permission"])
gate = module._permission_gate
audited = []

async def _audit(request, caller, permissions):
    audited.append(permissions)

async def _db(user_id):
    return ["knowledge.write", "admin.system", "allow_all"]

permission_gate.audit_permission_denied = _audit
gate._wire_rbac = lambda: None
gate._load_db_names = _db

ROLES = [r.value for r in Role] + ["Admin", "bogus", "", None]
SCOPES = {"session": None, "key-narrow": ["knowledge:read"], "key-admin": ["admin:*"], "key-legacy": "legacy"}
USER_IDS = {"no-db": None, "db": "00000000-0000-0000-0000-0000000000c3"}
REQUEST = types.SimpleNamespace(url=types.SimpleNamespace(path="/parity"), client=None, headers={})

def _caller(role, scopes, user_id):
    caller = {"sub": "parity"}
    if role is not None:
        caller["role"] = role
    if user_id:
        caller["user_id"] = user_id
    if scopes == "legacy":
        caller.update(api_key_id="k", scopes=[], legacy_full_scope=True)
    elif scopes is not None:
        caller.update(api_key_id="k", scopes=scopes)
    return caller

async def _verdict(caller, permission):
    try:
        await module.require_permission(permission)(request=REQUEST, caller=caller)
        return "allow"
    except Exception as exc:
        return str(getattr(exc, "status_code", type(exc).__name__))

async def main():
    out = {}
    for role in ROLES:
        for sname, scopes in SCOPES.items():
            for uname, uid in USER_IDS.items():
                caller = _caller(role, scopes, uid)
                for p in Permission:
                    out[f"{role}|{sname}|{uname}|{p.value}"] = await _verdict(caller, p)
    out["__audited__"] = len(audited)
    print("PARITY:" + json.dumps(out, sort_keys=True))

asyncio.run(main())
"""

_SERVICES = {
    "backend": ("autobot-backend", "auth_rbac"),
    "slm": ("autobot-slm-backend", "services.auth"),
}


def _probe(service: str) -> dict:
    directory, module = _SERVICES[service]
    env = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join((str(_ROOT), str(_ROOT / directory))),
        # The SLM config writes generated keys to disk when these are unset.
        SLM_SECRET_KEY="parity-probe-secret-key-0123456789abcdef",  # pragma: allowlist secret
        SLM_ENCRYPTION_KEY="parity-probe-encryption-key-0123456789ab",  # pragma: allowlist secret
    )
    result = subprocess.run(  # nosec B603  # fixed argv: this interpreter and the probe above
        [sys.executable, "-c", _PROBE, module],
        cwd=str(_ROOT / directory),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith("PARITY:")]
    assert result.returncode == 0 and lines, f"{service} probe failed:\n{result.stderr[-3000:]}"
    return json.loads(lines[-1][len("PARITY:") :])


@pytest.fixture(scope="module")
def verdicts() -> dict:
    return {service: _probe(service) for service in _SERVICES}


def test_both_services_give_identical_verdicts(verdicts):
    backend, slm = verdicts["backend"], verdicts["slm"]
    differing = sorted(k for k in backend if k != "__audited__" and backend[k] != slm.get(k))
    assert backend.keys() == slm.keys()
    assert differing == [], f"{len(differing)} rows differ, e.g. {differing[:5]}"


def test_every_denial_is_audited_in_both_services(verdicts):
    for service, table in verdicts.items():
        denials = sum(1 for k, v in table.items() if k != "__audited__" and v == "403")
        assert table["__audited__"] == denials, f"{service}: {denials} denials, {table['__audited__']} audited"


def test_the_table_is_a_real_mix_not_a_uniform_answer(verdicts):
    """Contrast: parity of a probe that refused everything would be meaningless."""
    counts = {
        v: sum(1 for k, x in verdicts["backend"].items() if k != "__audited__" and x == v) for v in ("allow", "403")
    }
    assert counts["allow"] > 100 and counts["403"] > 100, counts


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ("user|session|no-db|chat.use", "allow"),
        ("user|session|no-db|admin.system", "403"),
        ("user|session|db|admin.system", "allow"),  # DB grant unioned in (#18259)
        ("user|key-narrow|db|admin.system", "403"),  # key narrowing ANDed over a DB grant
        ("admin|key-narrow|no-db|knowledge.read", "allow"),
        ("admin|key-narrow|no-db|knowledge.write", "403"),
        ("readonly|key-admin|no-db|admin.system", "403"),  # a key never exceeds its owner
        ("Admin|session|no-db|chat.use", "403"),  # strict role reading
        ("None|session|no-db|chat.use", "403"),  # no legacy admin-flag fallback
        ("superadmin|session|no-db|admin.system", "403"),  # #13854: no granular permissions
    ],
)
def test_pinned_rows(verdicts, row, expected):
    for service, table in verdicts.items():
        assert table[row] == expected, f"{service}: {row} -> {table[row]}, expected {expected}"
