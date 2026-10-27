# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A Python client of the browser worker calls a route the worker registers (#19490).

Two clients posted to ``/scrape`` and ``/interact`` on the browser service port, but the canonical
server, ``autobot-browser-worker/playwright-server.js``, registers neither, so every request 404ed.
They now use ``/automation``. This guard reads the worker's ``app.get/post('<path>')`` routes, then
finds every f-string URL built on the browser port (``config.port.browser`` or
``BROWSER_SERVICE_PORT``), directly or through a variable assigned such a base URL. It fails on a
path the worker does not register.
"""

from __future__ import annotations

import ast
import re

from repo_tests._paths import repo_root

from tools.lint._scan_helpers import tracked_paths

_SERVER = "autobot-browser-worker/playwright-server.js"
_ROUTE = re.compile(r"""\bapp\.(?:get|post|put|delete)\(\s*['"](/[^'"]*)['"]""")
_ROOTS = ("autobot-backend", "autobot_shared", "autobot-infrastructure", "autobot-slm-backend", "scripts")
#: Tracked non-vendored Python under the roots; a sweep far below this read the wrong set.
MIN_FILES = 3000


def worker_routes(text: str) -> set[str]:
    """Every path ``playwright-server.js`` registers."""
    return set(_ROUTE.findall(text))


def _names_browser_port(node: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Attribute) and n.attr in {"browser", "BROWSER_SERVICE_PORT"})
        or (isinstance(n, ast.Name) and n.id == "BROWSER_SERVICE_PORT")
        for n in ast.walk(node)
    )


def _path_after(parts: list[ast.AST], index: int) -> str | None:
    """The ``/path`` literal right after the formatted value at *index*, cut at a query or fragment.

    A bare ``/`` followed by another formatted value (``f"{base}/{action}"``) is a dynamic route, which
    a static reader cannot judge; it is skipped.
    """
    if index + 1 < len(parts) and isinstance(parts[index + 1], ast.Constant):
        text = re.split(r"[?#]", str(parts[index + 1].value), maxsplit=1)[0]
        if text.startswith("/") and not (text == "/" and index + 2 < len(parts)):
            return text
    return None


def browser_paths(source: str) -> list[tuple[int, str]]:
    """``(line, path)`` of every browser-port URL path an f-string builds in *source*."""
    tree = ast.parse(source)
    bases = {
        t.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.JoinedStr) and _names_browser_port(n.value)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        for i, part in enumerate(node.values):
            if not isinstance(part, ast.FormattedValue):
                continue
            direct = _names_browser_port(part.value)
            via_base = isinstance(part.value, ast.Name) and part.value.id in bases
            path = _path_after(node.values, i) if (direct or via_base) else None
            if path:
                found.append((node.lineno, path))
    return found


def test_every_python_browser_client_calls_a_registered_route() -> None:
    root = repo_root()
    routes = worker_routes((root / _SERVER).read_text(encoding="utf-8"))
    assert {"/health", "/automation", "/navigate"} <= routes, "the route reader no longer sees the worker"
    paths = tracked_paths(root, *(f"{r}/*.py" for r in _ROOTS))
    assert len(paths) >= MIN_FILES, f"only {len(paths)} Python files swept -- the enumeration lost its reach"
    missing = []
    for rel in paths:
        source = (root / rel).read_text(encoding="utf-8")
        if "browser" not in source and "BROWSER_SERVICE_PORT" not in source:
            continue
        missing += [f"{rel}:{line}: {path}" for line, path in browser_paths(source) if path not in routes]
    assert missing == [], "the browser worker registers no such route:\n" + "\n".join(missing)


def test_a_scrape_client_is_caught_directly_and_through_a_base_url() -> None:
    source = (
        'base_url = f"http://localhost:{config.port.browser}"\n'
        'requests.post(f"{base_url}/scrape", json={})\n'
        'requests.post(f"http://localhost:{config.port.browser}/interact?x=1")\n'
        'requests.post(f"{base_url}/automation")\n'
        'requests.post(f"{other}/scrape")\n'
        'requests.post(f"{base_url}/{action}")\n'
    )
    assert browser_paths(source) == [(2, "/scrape"), (3, "/interact"), (4, "/automation")]
    assert {"/scrape", "/interact"} - worker_routes("app.post('/automation', h)\n") == {"/scrape", "/interact"}
