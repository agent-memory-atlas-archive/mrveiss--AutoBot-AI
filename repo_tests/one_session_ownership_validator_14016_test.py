# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""One `validate_session_ownership`, with named postures (#14016).

The conversation-file routes used to carry a second validator of the same name with another arity,
parameter order and return type: called positionally with the canonical order it swapped `request`
and `session_id`, a check that runs, passes and guards nothing. There is now ONE definition, in
`autobot-backend/security/session_ownership.py`, taking a `SessionPosture`; the file routes call it
with `SessionPosture.STRICT`. The detector reads the syntax tree, so prose or a string naming the
function is not a definition.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

_NAME = "validate_session_ownership"
_CANONICAL = "autobot-backend/security/session_ownership.py"


def _defines_name(source: str) -> bool:
    """True when `source` defines a function (or method) called `validate_session_ownership`."""
    return any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == _NAME for n in ast.walk(ast.parse(source))
    )


def _is_source_python(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return rel.endswith(".py") and not (name.endswith("_test.py") or name.startswith("test_") or "/tests/" in rel)


def _source_population(root: Path) -> list[str]:
    try:
        tracked = tracked_paths(root, "autobot-backend", "autobot-slm-backend", "autobot_shared")
    except EmptyEnumeration:
        return []
    return [rel for rel in tracked if _is_source_python(rel)]


REACH = declare(
    "session-ownership-validator-scan",
    discover=_source_population,
    # Mid-window, from REACH.window() = (2697, 2997, 2847).
    floor=2847,
    what="non-test python source",
    roots=("autobot-backend", "autobot-slm-backend", "autobot_shared"),
    growth=300,
)


@functools.lru_cache(maxsize=1)
def _definers() -> tuple[str, ...]:
    found = []
    for rel in REACH.examined(repo_root()):
        text = (repo_root() / rel).read_text(encoding="utf-8")
        if _NAME in text and _defines_name(text):
            found.append(rel)
    return tuple(found)


@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        ("a sync def", "def validate_session_ownership(a, b):\n    pass\n", True),
        ("an async def", "async def validate_session_ownership(a):\n    pass\n", True),
        ("a method", "class C:\n    def validate_session_ownership(self):\n        pass\n", True),
        ("an import", "from security.session_ownership import validate_session_ownership\n", False),
        ("a call", "async def f():\n    await validate_session_ownership('s', r)\n", False),
        ("a string and a comment", "# def validate_session_ownership\nx = 'def validate_session_ownership'\n", False),
        ("a differently named fork", "async def _authorize_session_for_user(r, s, u):\n    pass\n", False),
    ],
)
def test_the_matcher_tells_a_definition_from_a_use(label: str, source: str, expected: bool) -> None:
    assert _defines_name(source) is expected, label


def test_exactly_one_definition_and_it_is_the_canonical_module() -> None:
    assert _definers() == (_CANONICAL,), (
        f"#14016: {list(_definers())} define `{_NAME}`; it lives once in {_CANONICAL}. "
        "Import it, or give a differently-shaped helper its own private name."
    )


_FILE_ROUTES = "autobot-backend/api/conversation_files.py"


def _calls_without_strict(source: str) -> list[int]:
    """Lines of `validate_session_ownership(...)` calls that do not pass `posture=SessionPosture.STRICT`."""
    bad = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == _NAME:
            strict = any(
                kw.arg == "posture" and ast.unparse(kw.value) == "SessionPosture.STRICT" for kw in node.keywords
            )
            if not strict:
                bad.append(node.lineno)
    return bad


@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        ("strict", "validate_session_ownership(s, r, posture=SessionPosture.STRICT)\n", []),
        ("default", "validate_session_ownership(s, r)\n", [1]),
        ("another posture", "validate_session_ownership(s, r, posture=SessionPosture.DEFAULT)\n", [1]),
    ],
)
def test_the_strict_call_matcher_tells_strict_from_not(label: str, source: str, expected: list[int]) -> None:
    assert _calls_without_strict(source) == expected, label


def test_every_file_route_check_runs_under_the_strict_posture() -> None:
    source = (repo_root() / _FILE_ROUTES).read_text(encoding="utf-8")
    assert _NAME in source, "the file routes no longer reach the validator at all"
    assert _calls_without_strict(source) == []
