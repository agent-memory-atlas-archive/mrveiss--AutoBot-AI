# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The startup coordinator launches components from an argv list, never through a shell (#19496).

``subprocess.Popen(component.start_command, shell=True)`` was bandit B602 (High/High) and made every
PR touching the file red. The commands are fixed strings built in code, so no shell feature is
needed. ``autobot-infrastructure`` tests are not collected by CI (#14131), so this lives here.
"""

from __future__ import annotations

import ast

from repo_tests._paths import repo_root

_COORDINATOR = "autobot-infrastructure/shared/scripts/startup_coordinator.py"


def _tree() -> ast.Module:
    return ast.parse((repo_root() / _COORDINATOR).read_text(encoding="utf-8"))


def shell_true_calls(tree: ast.AST) -> list[int]:
    """Lines of each call passing ``shell=`` anything other than a literal False."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for k in node.keywords
        if k.arg == "shell" and not (isinstance(k.value, ast.Constant) and k.value.value is False)
    ]


def string_start_commands(tree: ast.AST) -> list[int]:
    """Lines where ``start_command=`` is given a string rather than an argv list."""
    return [
        k.value.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for k in node.keywords
        if k.arg == "start_command" and not isinstance(k.value, ast.List)
    ]


def test_the_coordinator_never_uses_a_shell() -> None:
    tree = _tree()
    assert shell_true_calls(tree) == []
    assert string_start_commands(tree) == []
    assert sum(1 for n in ast.walk(tree) if isinstance(n, ast.keyword) and n.arg == "start_command") >= 2


def test_the_detectors_see_a_shell_and_a_string_command() -> None:
    bad = ast.parse('subprocess.Popen(cmd, shell=True)\nComponentInfo(name="x", start_command="npm run dev")\n')
    assert shell_true_calls(bad) == [1]
    assert string_start_commands(bad) == [2]
    good = ast.parse('subprocess.Popen(cmd, shell=False)\nComponentInfo(name="x", start_command=["npm", "run"])\n')
    assert shell_true_calls(good) == [] and string_start_commands(good) == []
