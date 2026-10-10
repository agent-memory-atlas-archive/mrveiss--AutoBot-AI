# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A new audit store writer cannot appear outside the known set (#17225).

#6475 closed on three audit systems; there were eight stores by #17225. This pins the writers
that exist (see docs/developer/AUDIT_SYSTEMS.md) so a ninth is a deliberate, recorded act: add
the module to `_KNOWN_WRITERS` AND to the inventory, or route the record through
`services.audit.audit`. It does not merge any store; that needs an owner decision (#17038).

A "writer" is a non-test module that either
  * has a store-write call (`zadd`, `lpush`, ...) AND a string literal that starts with an audit
    key prefix, or
  * constructs `WorkflowAuditLog`, or
  * opens a file whose path expression names "audit" in append/write mode.
"""

import ast
import functools
from pathlib import Path

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

#: Modules that write an audit store today. Shrinks as D2/D3 of AUDIT_SYSTEMS.md land.
_KNOWN_WRITERS = frozenset(
    {
        "autobot-backend/services/audit/audit.py",
        "autobot-backend/services/audit/audit_log.py",
        "autobot-backend/services/audit_logger.py",
        "autobot-backend/services/event_log.py",
        "autobot-backend/knowledge/audit_log.py",
        "autobot-backend/security_layer.py",
        "autobot-backend/services/workflow_permission_service.py",
        # Not an audit record: code-audit daemon state that shares the `audit:` prefix (D4).
        "autobot-backend/workers/audit_tasks.py",
    }
)
_WRITE_VERBS = frozenset({"zadd", "lpush", "rpush", "xadd", "sadd", "hset", "setex", "set", "append"})
_KEY_PREFIXES = ("audit:", "audit_log:", "autobot:event_log")
_WRITE_MODES = ("a", "w", "ab", "wb", "a+", "w+")


def _is_source_python(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return rel.endswith(".py") and not (name.endswith("_test.py") or name.startswith("test_") or "/tests/" in rel)


def _has_key_literal(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith(_KEY_PREFIXES):
            return True
        if isinstance(node, ast.JoinedStr) and node.values:
            head = node.values[0]
            if isinstance(head, ast.Constant) and str(head.value).startswith(_KEY_PREFIXES):
                return True
    return False


def _names_audit(expr: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Attribute) and "audit" in n.attr.lower())
        or (isinstance(n, ast.Name) and "audit" in n.id.lower())
        for n in ast.walk(expr)
    )


def _mode_of(call: ast.Call) -> str | None:
    arg = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "mode"), None)
    return arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None


def _is_call(node: ast.AST, *, attr_in: frozenset[str] = frozenset(), name: str = "") -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (isinstance(f, ast.Attribute) and f.attr in attr_in) or (isinstance(f, ast.Name) and f.id == name)


def is_audit_writer(source: str) -> bool:
    """True when `source` writes an audit store by one of the three shapes in the module doc."""
    tree = ast.parse(source)
    nodes = list(ast.walk(tree))
    if any(_is_call(n, name="WorkflowAuditLog") for n in nodes):
        return True
    for n in nodes:
        if _is_call(n, name="open") and n.args and _mode_of(n) in _WRITE_MODES and _names_audit(n.args[0]):
            return True
    return any(_is_call(n, attr_in=_WRITE_VERBS) for n in nodes) and _has_key_literal(tree)


@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        ("redis zadd on an audit key", 'async def f(r):\n    await r.zadd("audit:x", {"a": 1})\n', True),
        (
            "f-string audit key via a variable",
            'def f(r, u):\n    k = f"audit_log:{u}"\n    r.zadd(k, {"a": 1})\n',
            True,
        ),
        ("orm row", "def f(s):\n    s.add(WorkflowAuditLog(user_id=1))\n", True),
        ("jsonl append", 'def f(self):\n    open(self.audit_log_file, "a")\n', True),
        ("reads an audit key only", 'def f(r):\n    return r.zrange("audit:x", 0, -1)\n', False),
        ("writes an unrelated key", 'def f(r):\n    r.zadd("queue:x", {"a": 1})\n', False),
        ("reads an audit file", 'def f(self):\n    open(self.audit_log_file, "r")\n', False),
        ("appends to an unrelated file", 'def f():\n    open("out.txt", "a")\n', False),
    ],
)
def test_the_matcher_tells_a_writer_from_a_reader(label: str, source: str, expected: bool) -> None:
    assert is_audit_writer(source) is expected, label


def _population(root: Path) -> list[str]:
    try:
        tracked = tracked_paths(root, "autobot-backend", "autobot-slm-backend", "autobot_shared")
    except EmptyEnumeration:
        return []
    return [rel for rel in tracked if _is_source_python(rel)]


REACH = declare(
    "audit-writer-single-source",
    discover=_population,
    # Mid-window, from REACH.window(): window (2697, 2997, 2847) at authoring.
    floor=2847,
    what="non-test python source",
    roots=("autobot-backend", "autobot-slm-backend", "autobot_shared"),
    growth=300,
)


@functools.lru_cache(maxsize=1)
def _writers() -> tuple[str, ...]:
    root = repo_root()
    found = []
    for rel in REACH.examined(root):
        text = (root / rel).read_text(encoding="utf-8")
        if "audit" in text or "event_log" in text:
            if is_audit_writer(text):
                found.append(rel)
    return tuple(sorted(found))


def test_no_audit_writer_outside_the_known_set() -> None:
    new = sorted(set(_writers()) - _KNOWN_WRITERS)
    assert not new, (
        f"#17225: {new} write an audit store. Route the record through services.audit.audit "
        "(emit/record) instead of a new store, or, if a new store is a recorded decision, add the "
        "module to _KNOWN_WRITERS and docs/developer/AUDIT_SYSTEMS.md."
    )


def test_every_known_writer_still_writes() -> None:
    """A writer that stopped writing, or moved, must leave the set; a stale entry hides a new one."""
    gone = sorted(_KNOWN_WRITERS - set(_writers()))
    assert not gone, f"#17225: {gone} no longer match as audit writers; remove them from _KNOWN_WRITERS."
