# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""No new hand-rolled fake ``AsyncSession`` in a test; the one fake lives in testkit (#18883).

`autobot-backend/testkit/fake_async_session.py` is the one fake. Hand-rolled `_Session` classes
drifted from the real `AsyncSession` signature (a missing `attribute_names`, a keyword-only
`refresh`, a positional `with_for_update` on `get`), so a test passed on calls production
cannot make.

WHAT MATCHES (syntax tree only, so a comment or string naming these is not a site)
    A class in a test file whose OWN body defines `async def commit` or `async def refresh`
    and which does not list `FakeAsyncSession` among its bases. A nested class is judged on its
    own body. Each is keyed `path::enclosing_function::ClassName`, so several `_Session`
    classes in one file are distinct (`<module>` when defined at module level).
BOUNDARY
    Scanned: tracked test `.py` (`*_test.py`, `test_*.py`, under `/tests/`, `conftest.py`) in
    `SCAN_ROOTS`. Exempt by path: the canonical fake.
    NOT seen: a fake that defines neither method (a bare `execute` stub), one built with
    `MagicMock`/`AsyncMock`, and a test double outside `SCAN_ROOTS`. Those are separate findings.

THE ALLOWLIST (`hand_rolled_async_session_baseline_18883.py`) freezes today's offenders. It may
only shrink: a new match fails, a stale entry fails. Migrating a file deletes its lines.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare
from repo_tests.hand_rolled_async_session_baseline_18883 import ALLOWLIST, FROZEN_TOTAL

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

CANONICAL = "autobot-backend/testkit/fake_async_session.py"
CANONICAL_NAME = "FakeAsyncSession"
SCAN_ROOTS = ("autobot-backend", "autobot-slm-backend")
_SESSION_METHODS = frozenset({"commit", "refresh"})


def _is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return name.endswith("_test.py") or name.startswith("test_") or name == "conftest.py" or "/tests/" in rel


def _is_scanned(rel: str) -> bool:
    return rel.endswith(".py") and rel != CANONICAL and _is_test_file(rel)


def _defines_session_method(cls: ast.ClassDef) -> bool:
    return any(isinstance(n, ast.AsyncFunctionDef) and n.name in _SESSION_METHODS for n in cls.body)


def _extends_canonical(cls: ast.ClassDef) -> bool:
    return any(ast.unparse(base).split(".")[-1] == CANONICAL_NAME for base in cls.bases)


def _visit(node: ast.AST, enclosing: str, found: list[tuple[str, str]]) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef) and _defines_session_method(child) and not _extends_canonical(child):
            found.append((enclosing, child.name))
        scope = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else enclosing
        _visit(child, scope, found)


def hand_rolled_sessions(source: str) -> list[tuple[str, str]]:
    """`(enclosing function, class name)` for every hand-rolled session class in `source`."""
    found: list[tuple[str, str]] = []
    _visit(ast.parse(source), "<module>", found)
    return found


def _population(root: Path) -> list[str]:
    try:
        tracked = tracked_paths(root, *SCAN_ROOTS)
    except EmptyEnumeration:
        return []
    return [rel for rel in tracked if _is_scanned(rel)]


REACH = declare(
    "hand-rolled-async-session-scan",
    discover=_population,
    # Mid-window, from REACH.window(): population 2406 at 6fe63f6480 -> (2206, 2406, 2306).
    floor=2306,
    what="test python source",
    roots=SCAN_ROOTS,
    growth=200,
)


@functools.lru_cache(maxsize=1)
def _found() -> frozenset[str]:
    keys: set[str] = set()
    for rel in REACH.examined(repo_root()):
        source = (repo_root() / rel).read_text(encoding="utf-8")
        keys.update(f"{rel}::{func}::{name}" for func, name in hand_rolled_sessions(source))
    return frozenset(keys)


_HAND_ROLLED = """
def make():
    class _Session:
        async def commit(self):
            pass
        async def refresh(self, obj):
            pass
    return _Session()
"""
_TWO_IN_ONE_FILE = """
def a():
    class _Session:
        async def commit(self): ...
def b():
    class _Session:
        async def refresh(self, o): ...
"""
_MODULE_LEVEL = "class Fake:\n    async def commit(self):\n        pass\n"
_SUBCLASS = "from testkit.fake_async_session import FakeAsyncSession\nclass S(FakeAsyncSession):\n    async def commit(self):\n        pass\n"
_QUALIFIED = "import testkit.fake_async_session as t\nclass S(t.FakeAsyncSession):\n    async def refresh(self, o):\n        pass\n"


@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        ("a class inside a function", _HAND_ROLLED, [("make", "_Session")]),
        ("same name, two functions", _TWO_IN_ONE_FILE, [("a", "_Session"), ("b", "_Session")]),
        ("module level", _MODULE_LEVEL, [("<module>", "Fake")]),
        ("a subclass of the canonical fake", _SUBCLASS, []),
        ("a qualified canonical base", _QUALIFIED, []),
        ("a sync commit is not a session", "class C:\n    def commit(self):\n        pass\n", []),
        ("an unrelated async method", "class C:\n    async def execute(self):\n        pass\n", []),
        ("comments and strings only", '# async def commit\nclass C:\n    """async def refresh"""\n', []),
        (
            "a nested class is judged on its own body",
            "class Outer:\n    class Inner:\n        async def commit(self): ...\n",
            [("<module>", "Inner")],
        ),
    ],
)
def test_the_detector_tells_a_hand_rolled_session_from_a_lookalike(
    label: str, source: str, expected: list[tuple[str, str]]
) -> None:
    assert hand_rolled_sessions(source) == expected, label


def test_the_canonical_module_is_exempt_and_only_test_files_are_scanned() -> None:
    assert not _is_scanned(CANONICAL)
    assert _is_scanned("autobot-backend/foo/x_test.py")
    assert _is_scanned("autobot-backend/foo/test_x.py")
    assert _is_scanned("autobot-backend/foo/tests/helpers.py")
    assert _is_scanned("autobot-slm-backend/conftest.py")
    assert not _is_scanned("autobot-backend/foo/service.py")


def test_the_scan_is_not_vacuous() -> None:
    """The scan must see the known offenders: a blind scan reports an empty, clean tree."""
    found = _found()
    assert len(found) >= 1, "no hand-rolled session found; the scan stopped reaching the tree or all were migrated"
    assert len(REACH.examined(repo_root())) > 2000


def test_no_new_hand_rolled_async_session() -> None:
    new = sorted(_found() - set(ALLOWLIST))
    assert not new, (
        f"#18883: {new} define a fake async session (commit/refresh) by hand. Use {CANONICAL} "
        f"({CANONICAL_NAME}, FakeResult); pass opt-ins through its constructor."
    )


def test_every_allowlist_entry_still_matches() -> None:
    stale = sorted(set(ALLOWLIST) - _found())
    assert not stale, f"stale allowlist entries (class gone or migrated); delete these lines: {stale}"


def test_the_allowlist_only_shrinks_and_every_entry_has_a_reason() -> None:
    assert len(ALLOWLIST) <= FROZEN_TOTAL, f"{len(ALLOWLIST)} entries exceeds the frozen {FROZEN_TOTAL}"
    assert all(reason.strip() for reason in ALLOWLIST.values())
