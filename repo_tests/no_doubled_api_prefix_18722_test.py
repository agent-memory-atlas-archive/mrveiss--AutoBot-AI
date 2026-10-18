# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""No router below ``/api`` declares a path or prefix that starts with ``/api`` (#18722).

Every router under ``autobot-backend/api`` and ``autobot-slm-backend/api`` is mounted below
``/api``. A decorator such as ``@router.get("/api/cache/stats")`` is therefore served at
``/api/system/api/cache/stats``: thirteen routes shipped that way. The detector reads the syntax
tree, so a docstring or comment showing such a path is not a declaration.

A segment named ``/api-keys`` or ``/api-endpoints`` is a different word, not the doubled prefix:
the match is ``/api`` followed by ``/`` or the end of the string.

``_ALLOWED`` is the explicit exception list (``(file, path) -> reason``). It is empty: the one
module that still names the doubled paths, as the sources of a 308 shim, does so in a dict
handed to ``add_deprecated_redirects``, not in a decorator, so it is not a declaration.
"""

from __future__ import annotations

import ast
import functools
import re

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

_ROOTS = ("autobot-backend/api", "autobot-slm-backend/api")
_DOUBLED = re.compile(r"^/api(/|$)")
_ROUTE_METHODS = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options", "websocket", "api_route", "add_api_route"}
)
_ROUTER_NAME = re.compile(r"(^|_)router$")

#: ``(repo-relative file, declared path) -> reason``. Empty by design; see the module docstring.
_ALLOWED: dict[tuple[str, str], str] = {}


def _is_source_python(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return rel.endswith(".py") and not (name.endswith("_test.py") or name.startswith("test_") or "/tests/" in rel)


def _population(root) -> list[str]:
    try:
        tracked = tracked_paths(root, *_ROOTS)
    except EmptyEnumeration:
        return []
    return [rel for rel in tracked if _is_source_python(rel)]


REACH = declare(
    "doubled-api-prefix-scan",
    discover=_population,
    # Mid-window, from REACH.window(); the arithmetic is on the PR, not here.
    floor=473,
    what="non-test python source",
    roots=_ROOTS,
    growth=60,
)


def _str_const(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _receiver_is_router(func: ast.Attribute) -> bool:
    base = func.value
    return isinstance(base, ast.Name) and bool(_ROUTER_NAME.search(base.id))


def _declared_path(call: ast.Call) -> str | None:
    """The route path a ``<x>router.<method>(...)`` call declares, else None."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr in _ROUTE_METHODS and _receiver_is_router(func)):
        return None
    if call.args:
        return _str_const(call.args[0])
    return next((_str_const(k.value) for k in call.keywords if k.arg == "path"), None)


def _is_router_ctor(call: ast.Call) -> bool:
    func = call.func
    return (isinstance(func, ast.Name) and func.id == "APIRouter") or (
        isinstance(func, ast.Attribute) and func.attr == "APIRouter"
    )


def doubled_paths(source: str) -> list[str]:
    """Every route path and ``APIRouter(prefix=...)`` in `source` that starts with ``/api``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        path = _declared_path(node)
        if path is None and _is_router_ctor(node):
            path = next((_str_const(k.value) for k in node.keywords if k.arg == "prefix"), None)
        if path is not None and _DOUBLED.match(path):
            found.append(path)
    return found


@functools.lru_cache(maxsize=1)
def _offenders() -> tuple[tuple[str, str], ...]:
    root = repo_root()
    hits = []
    for rel in REACH.examined(root):
        text = (root / rel).read_text(encoding="utf-8")
        if "/api" not in text:
            continue
        hits.extend((rel, p) for p in doubled_paths(text) if (rel, p) not in _ALLOWED)
    return tuple(hits)


#: The planted control: the exact shapes that shipped, each of which the detector must flag.
_PLANTED = (
    ('@router.get("/api/cache/stats")\nasync def f(): ...\n', ["/api/cache/stats"]),
    (
        '@router.post("/api/knowledge/facts/{fact_id}/share")\nasync def f(): ...\n',
        ["/api/knowledge/facts/{fact_id}/share"],
    ),
    ('router = APIRouter(tags=["x"], prefix="/api")\n', ["/api"]),
    ('router.add_api_route(path="/api/x", endpoint=f)\n', ["/api/x"]),
    ('@my_router.websocket("/api/ws")\nasync def f(): ...\n', ["/api/ws"]),
)

#: Contrast: shapes that must NOT be flagged.
_CLEAN = (
    '@router.get("/cache/stats")\nasync def f(): ...\n',
    '@router.get("/api-keys")\nasync def f(): ...\n',
    'router = APIRouter(prefix="/api-endpoints")\n',
    '"""@router.get("/api/cache/stats")"""\nx = 1\n',
    '# @router.get("/api/cache/stats")\nx = 1\n',
    'client.get("/api/health")\n',
    'add_deprecated_redirects(router, {("GET", "/api/cache/stats"): "/cache/stats"})\n',
)


@pytest.mark.parametrize(("source", "expected"), _PLANTED)
def test_the_detector_flags_each_planted_doubled_declaration(source: str, expected: list[str]) -> None:
    assert doubled_paths(source) == expected


@pytest.mark.parametrize("source", _CLEAN)
def test_the_detector_leaves_clean_shapes_alone(source: str) -> None:
    assert doubled_paths(source) == []


def test_the_scan_is_not_vacuous() -> None:
    """A tree of routers is the input: fewer than 100 route-declaring modules is a blind scan."""
    root = repo_root()
    routers = sum(1 for rel in REACH.examined(root) if "APIRouter" in (root / rel).read_text(encoding="utf-8"))
    assert routers >= 100, f"only {routers} modules under the api/ roots build an APIRouter"


def test_no_router_declares_a_path_starting_with_api() -> None:
    assert not _offenders(), (
        "#18722: these declare a path or prefix starting with /api inside a router that is already "
        f"mounted under /api, so they are served at /api/<mount>/api/...: {list(_offenders())}. "
        "Drop the /api and, for a path that already shipped, add_deprecated_redirects()."
    )
