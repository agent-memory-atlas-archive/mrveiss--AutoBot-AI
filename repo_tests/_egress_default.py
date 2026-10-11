# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""AST detector for outbound HTTP calls and whether each carries an egress policy (#17576, AC4).

Separate from the guard that consumes it, for the reason ``_raw_aiohttp_session.py`` gives: the
matcher and the data it is judged against live in different files.

**What counts as an outbound call** (kind in brackets):

- ``manager``: ``request`` / ``tracked_request`` / ``get_json`` / ``post_json`` / ``get`` / ``post`` ...
  on a receiver that names the shared client (``get_http_client()``, ``http_client``, ``self._http``...).
- ``session``: ``get`` / ``post`` / ``request`` / ``ws_connect`` ... on a session-named receiver in a
  file that uses aiohttp.
- ``httpx``, ``requests``, ``urllib``: the library's call surface.

**What counts as guarded.** A ``manager`` call passing ``guard_egress=`` with anything other than a
literal ``None``; any other kind sitting in a function that calls ``assert_egress_policy`` earlier
in the same function. Everything else is unguarded -- including ``guard_egress=None``, which is the
manager's "no guard" spelled out.

The receiver heuristics deliberately over-match (a ``client.get`` that is not HTTP is counted): a
false positive is a red that names its file, a false negative is the confident zero this guard exists
to prevent. Nothing here reads the repository; callers hand in source text or an explicit file list.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable, NamedTuple

__all__ = ["Site", "outbound_sites", "scan_paths"]

_VERBS = {"get", "post", "put", "delete", "patch", "head", "request", "stream", "ws_connect"}
_MANAGER_ONLY = {"tracked_request", "get_json", "post_json"}
_MANAGER_RECV = re.compile(r"http_client|get_http_client\(\)|http_manager|HTTPClientManager|self\._?http\b")
_SESSION_RECV = re.compile(r"(^|\.)_?(session|sess|client)$", re.I)
_QUICK = re.compile(
    "|".join(
        ("tracked_request", "get_http_client", "HTTPClientManager", "http_client", "ClientSession", "tracked_session")
        + ("aiohttp", "httpx", "authlib", "requests", "urlopen", "get_json", "post_json")
    )
)
_POLICY_CALL = "assert_egress_policy"


class Site(NamedTuple):
    """One outbound call: 1-based line, client kind, and whether it carries a policy."""

    lineno: int
    kind: str
    guarded: bool


def _call_name(func: ast.expr) -> str:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _guard_kwarg_present(call: ast.Call) -> bool:
    """``guard_egress=<anything but a literal None>``."""
    for kw in call.keywords:
        if kw.arg == "guard_egress":
            return not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
    return False


def _classify(call: ast.Call, imports: set[str], src_has_aiohttp: bool) -> str | None:
    """Client kind for *call*, or ``None`` when it is not an outbound HTTP call."""
    func = call.func
    name = _call_name(func)
    if name == "urlopen":
        return "urllib"
    if not isinstance(func, ast.Attribute) or (name not in _VERBS and name not in _MANAGER_ONLY):
        return None
    recv = ast.unparse(func.value)
    if name in _MANAGER_ONLY:
        return "manager"
    if not call.args and not call.keywords:
        return None  # ``x.get()`` -- a getter, not a request
    if recv == "requests" or (recv.endswith(("session", "Session")) and "requests" in imports and not src_has_aiohttp):
        return "requests" if "requests" in imports else None
    if recv.startswith("httpx") or (bool(imports & {"httpx", "authlib"}) and _SESSION_RECV.search(recv)):
        return "httpx"
    if _MANAGER_RECV.search(recv):
        return "manager"
    if src_has_aiohttp and _SESSION_RECV.search(recv):
        return "session"
    return None


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def _policy_lines(scope: ast.AST) -> list[int]:
    """Lines of every ``assert_egress_policy`` call lexically inside *scope*."""
    return [n.lineno for n in ast.walk(scope) if isinstance(n, ast.Call) and _call_name(n.func) == _POLICY_CALL]


def _walk(node: ast.AST, scope: ast.AST) -> Iterable[tuple[ast.Call, ast.AST]]:
    """Every ``Call`` with its nearest enclosing function (or the module)."""
    for child in ast.iter_child_nodes(node):
        inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else scope
        if isinstance(child, ast.Call):
            yield child, scope
        yield from _walk(child, inner)


def outbound_sites(source: str) -> tuple[Site, ...]:
    """Every outbound call in *source*. Raises ``SyntaxError`` rather than returning ``()``."""
    tree = ast.parse(source)
    imports = _imports(tree)
    # Text, not imports: a session passed in as a parameter (``session: Any``) is
    # aiohttp's whether or not the module imports it -- only its docstring says so.
    has_aiohttp = "aiohttp" in source or "tracked_session" in source or "http_client" in source
    sites = []
    for call, scope in _walk(tree, tree):
        kind = _classify(call, imports, has_aiohttp)
        if kind is None:
            continue
        if kind == "manager":
            guarded = _guard_kwarg_present(call)
        else:
            guarded = any(line <= call.lineno for line in _policy_lines(scope))
        sites.append(Site(call.lineno, kind, guarded))
    return tuple(sorted(sites))


def scan_paths(root: Path, relative_paths: Iterable[str]) -> dict[str, tuple[Site, ...]]:
    """Map each path with outbound calls to them; an unparsable file gets an ``(UNPARSED)`` key."""
    found: dict[str, tuple[Site, ...]] = {}
    for rel in relative_paths:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        if not _QUICK.search(text):
            continue  # speed gate on vocabulary, not a matcher
        try:
            sites = outbound_sites(text)
        except SyntaxError:
            found[f"{rel} (UNPARSED)"] = ()
            continue
        if sites:
            found[rel] = sites
    return found
