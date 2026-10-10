# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Container hardening kwargs are built once (#17267).

`autobot_shared/container_hardening.py` is the only place that may spell the Docker
hardening kwargs ``cap_drop`` / ``security_opt``. A client that writes either one as a
dict key, a subscript target or a keyword argument is building the constraint set
inline again. The detector reads the syntax tree, so prose naming them is not a copy.
"""

from __future__ import annotations

import ast
import functools

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

_CANONICAL = "autobot_shared/container_hardening.py"
_KEYS = frozenset({"cap_drop", "security_opt"})

#: The two clients that create hardened containers must keep taking them from the builder.
_CONSUMERS = ("autobot-backend/secure_sandbox_executor.py", "autobot-backend/services/docker_task_workspace.py")


def _is_key(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value in _KEYS


def builds_hardening_inline(source: str) -> bool:
    """True when `source` spells a hardening kwarg as a dict key, subscript target or keyword."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict) and any(_is_key(k) for k in node.keys):
            return True
        if isinstance(node, ast.keyword) and node.arg in _KEYS:
            return True
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and _is_key(node.slice):
            return True
    return False


def _calls_builder(source: str) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "hardening_kwargs"
        for n in ast.walk(ast.parse(source))
    )


@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        ("dict literal", 'cfg = {"cap_drop": ["ALL"]}\n', True),
        ("security_opt key", 'cfg = {"image": "x", "security_opt": ["no-new-privileges"]}\n', True),
        ("subscript store", 'cfg["cap_drop"] = ["ALL"]\n', True),
        ("keyword argument", 'client.containers.run(image, cap_drop=["ALL"])\n', True),
        ("builder spread", "cfg = {**hardening_kwargs(network_mode='none', read_only=True)}\n", False),
        ("subscript read", 'x = cfg["cap_drop"]\n', False),
        ("comment naming it", '# "cap_drop": ["ALL"]\nx = 1\n', False),
        ("docstring naming it", '"""sets cap_drop=ALL"""\nx = 1\n', False),
    ],
)
def test_the_matcher_tells_an_inline_build_from_a_use(label: str, source: str, expected: bool) -> None:
    assert builds_hardening_inline(source) is expected, label


def _is_source_python(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return rel.endswith(".py") and not (name.endswith("_test.py") or name.startswith("test_") or "/tests/" in rel)


def _source_population(root) -> list[str]:
    try:
        tracked = tracked_paths(root, "autobot-backend", "autobot_shared")
    except EmptyEnumeration:
        return []
    return [rel for rel in tracked if _is_source_python(rel)]


REACH = declare(
    "container-hardening-inline",
    discover=_source_population,
    # Mid-window, from REACH.window(); the arithmetic is on the PR, not here.
    floor=2580,
    what="non-test python source",
    roots=("autobot-backend", "autobot_shared"),
    growth=300,
)


@functools.lru_cache(maxsize=1)
def _inline_builders() -> tuple[str, ...]:
    root = repo_root()
    return tuple(
        rel
        for rel in REACH.examined(root)
        if rel != _CANONICAL and builds_hardening_inline((root / rel).read_text(encoding="utf-8"))
    )


def test_no_client_builds_hardening_kwargs_inline() -> None:
    assert not _inline_builders(), (
        f"#17267: {list(_inline_builders())} spell cap_drop/security_opt directly; "
        f"take them from `hardening_kwargs(...)` in {_CANONICAL}."
    )


@pytest.mark.parametrize("rel", _CONSUMERS)
def test_each_hardened_client_calls_the_builder(rel: str) -> None:
    assert _calls_builder(
        (repo_root() / rel).read_text(encoding="utf-8")
    ), f"#17267: {rel} no longer calls hardening_kwargs"


def test_the_canonical_module_does_build_them() -> None:
    assert builds_hardening_inline((repo_root() / _CANONICAL).read_text(encoding="utf-8"))
