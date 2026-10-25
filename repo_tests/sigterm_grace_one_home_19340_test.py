# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The LLC SIGTERM grace has ONE home: ``llc/adapters/subprocess_base.SIGTERM_GRACE_SECONDS`` (#19340).

``process_adapter.py`` killed a cancelled child after 5 s while every subprocess-lifecycle
adapter waited 10 s, so a process needing 7 s to shut down died under one adapter and
survived under the other. Both now pass the one constant to ``terminate_pid``.

A copy comes back in two shapes, and both are flagged anywhere under ``autobot-backend/llc``:
a numeric literal bound to a ``*SIGTERM_GRACE*`` name outside the home, and a literal grace
passed straight to ``terminate_pid``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from repo_tests._paths import repo_root
from repo_tests._reach import declare

_LLC = "autobot-backend/llc"
_HOME = "autobot-backend/llc/adapters/subprocess_base.py"


def _modules(root: Path) -> list[Path]:
    return sorted(p for p in (root / _LLC).rglob("*.py") if "tests" not in p.parts and not p.name.endswith("_test.py"))


#: 216 non-test LLC modules measured at #19340.
REACH = declare("llc-sigterm-grace-copies", discover=_modules, floor=180, what="non-test LLC modules")


def _is_number(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)


def copies(source: str, rel: str) -> list[str]:
    """Each literal grace in *source*: a ``*SIGTERM_GRACE*`` literal binding, or a literal ``terminate_pid`` grace."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and rel != _HOME:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id for t in targets if isinstance(t, ast.Name) and "SIGTERM_GRACE" in t.id.upper()]
            if names and _is_number(node.value):
                found.append(f"{rel}:{node.lineno}: {names[0]} = {node.value.value}")
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "terminate_pid":
            grace = (
                node.args[1]
                if len(node.args) > 1
                else next((k.value for k in node.keywords if k.arg == "grace_seconds"), None)
            )
            if _is_number(grace):
                found.append(f"{rel}:{node.lineno}: terminate_pid(..., {grace.value}, ...)")
    return found


def test_the_grace_has_one_home_on_the_live_tree() -> None:
    root = repo_root()
    files = REACH.examined(root)
    found = [
        hit for path in files for hit in copies(path.read_text(encoding="utf-8"), path.relative_to(root).as_posix())
    ]
    REACH.completed(files)
    assert not found, f"import SIGTERM_GRACE_SECONDS from {_HOME} instead: {found}"


def test_both_terminate_pid_callers_import_the_home() -> None:
    """Both callers name the home's constant -- the 5 s copy cannot return as an alias of something else."""
    for rel in (
        "autobot-backend/llc/adapters/process_adapter.py",
        "autobot-backend/llc/adapters/claude_code_adapter.py",
    ):
        tree = ast.parse((repo_root() / rel).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess_base"
            for alias in node.names
        }
        assert "SIGTERM_GRACE_SECONDS" in imported, rel


def test_a_restated_literal_is_flagged() -> None:
    assert copies("_SIGTERM_GRACE_SECONDS = 5\n", "autobot-backend/llc/adapters/process_adapter.py")


def test_a_literal_grace_passed_to_terminate_pid_is_flagged() -> None:
    assert copies("async def f():\n    await terminate_pid(pid, 5, 'X', t)\n", "autobot-backend/llc/x.py")
    assert copies(
        "async def f():\n    await s.terminate_pid(pid, grace_seconds=7, log_name='X', expected_create_time=t)\n", "x"
    )


def test_the_home_may_define_it_and_an_import_is_not_a_copy() -> None:
    assert not copies("SIGTERM_GRACE_SECONDS = 10\n", _HOME)
    assert not copies(
        "from .subprocess_base import SIGTERM_GRACE_SECONDS\nawait terminate_pid(p, SIGTERM_GRACE_SECONDS, 'X', t)\n",
        "autobot-backend/llc/adapters/process_adapter.py",
    )
