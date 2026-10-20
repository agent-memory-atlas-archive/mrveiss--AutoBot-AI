# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Detector for a test file that builds a git repo without the isolated env (#18940).

A file-level rule, because the call-level shape varies too much to follow: a literal
``["git", "init"]`` argv, a ``_git(repo, "commit")`` helper, a ``bash -c "git init"`` string.
A file BUILDS a repo when it imports ``subprocess``, names ``init`` or ``commit`` as an
argument or argv element, and names ``git``. It is COMPLIANT when it imports from ``autobot_shared.isolated_git``.

KNOWN GAP: a file that imports the helper but also runs one raw ``git commit`` is accepted; the
call-level scrub is ``tools/lint/check_git_write_env_scrubbed.py``'s job (#15246).
"""

from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath

HELPER_MODULE = "autobot_shared.isolated_git"
_VERBS = frozenset({"init", "commit"})
_SHELL_GIT = re.compile(r"\bgit\s+(?:-\S+\s+)*(?:init|commit)\b")
_SKIP_PARTS = frozenset({"node_modules", ".git", ".worktrees", "__pycache__"})


def is_test_file(rel: str) -> bool:
    """A test module or conftest, by repo-relative name."""
    name = PurePosixPath(rel).name
    return name.endswith("_test.py") or name.startswith("test_") or name == "conftest.py"


def is_skipped(rel: str) -> bool:
    """True when a repo-RELATIVE path part is a skipped directory (#18972)."""
    return bool(_SKIP_PARTS & set(PurePosixPath(rel).parts[:-1]))


def _strings(elements: list[ast.expr]) -> set[str]:
    return {e.value for e in elements if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def _names_verb(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _VERBS & _strings(node.args):
            return True
        if isinstance(node, ast.List) and _VERBS & _strings(node.elts):
            return True
    return False


def _mentions_git(tree: ast.Module) -> bool:
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and "git" in n.value for n in ast.walk(tree))


def _shell_builds_repo(tree: ast.Module) -> bool:
    """``["bash", "-c", "git init ..."]``: the script string right after a ``-c`` argv element."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.List):
            continue
        for flag, script in zip(node.elts, node.elts[1:]):
            if _is_str(flag, "-c") and isinstance(script, ast.Constant) and isinstance(script.value, str):
                if _SHELL_GIT.search(script.value):
                    return True
    return False


def _is_str(node: ast.expr, value: str) -> bool:
    return isinstance(node, ast.Constant) and node.value == value


def _imports_subprocess(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "subprocess" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            return True
    return False


def builds_git_repo(tree: ast.Module) -> bool:
    """True when *tree* spawns processes and names ``init``/``commit`` as a git argument."""
    if not _imports_subprocess(tree):
        return False
    return (_names_verb(tree) and _mentions_git(tree)) or _shell_builds_repo(tree)


def imports_helper(tree: ast.Module) -> bool:
    """True when *tree* imports from :data:`HELPER_MODULE`."""
    return any(isinstance(n, ast.ImportFrom) and n.module == HELPER_MODULE for n in ast.walk(tree))


def offends(source: str) -> bool:
    """True for a repo-building test file that does not use the isolated helper."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return builds_git_repo(tree) and not imports_helper(tree)
