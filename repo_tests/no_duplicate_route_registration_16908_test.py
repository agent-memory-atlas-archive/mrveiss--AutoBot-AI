# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""No (method, path) is registered twice in either app (#16908, #17325).

Starlette answers a request from the FIRST route that matches, so a second registration of the same
(method, path) is unreachable -- dead code that reads as a live endpoint, and a duplicate OpenAPI operation.
Which copy won was decided by the order of two lists in two files, and nothing checked it.

The BACKEND is read from the real router objects: ``load_core_routers()`` and ``load_optional_routers()`` are the
two calls ``app_factory.py`` mounts from, and ``autobot_shared/api_routing/router_routes.effective_routes()``
resolves nested ``include_router`` with its prefix. The design is the one commit ``f4e2d6badb`` (#16908) built and
never landed; this revives it against the current loaders and replaces the AST guard, because two guards for one
property is a fork. The AST read it replaces was blind to ``add_api_route``, loop-built routes and computed
prefixes, and counted a router mounted twice as one entry. Here every occurrence is counted.

The SLM backend is still read with ``autobot_shared/api_routing/route_order.py`` (source, ``ast``): it has no
router-loader pair to call, so the real-object read is not available for it (tracked by #19033).

What it cannot see, stated so a green run reads correctly: the three routers ``app_factory.py`` mounts
directly (``/v1`` OpenAI and Anthropic compat, ``/.well-known`` JWKS), the SLM's runtime-only routes, and any
route a router adds after import. A route whose mount path cannot be recovered is asserted empty, never skipped.
"""

from __future__ import annotations

import collections
import functools
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from repo_tests._paths import repo_root

from autobot_shared.api_routing.route_order import OrderedRoute, RouteOrder, slm_routes
from autobot_shared.api_routing.router_routes import effective_routes

_BACKEND = "autobot-backend"
_SLM = "autobot-slm-backend"
_ROOT = repo_root()

#: Handler-level duplicates: two DIFFERENT implementations on one (method, path), not a double mount.
#: Resolving them is a judgement (they differ in declared auth and capability), owned by #18721 -- decision
#: pending. SHRINK-ONLY: entries leave when #18721 lands, and none may be added; a new duplicate is what
#: this guard is for.
#: Population floors, measured 2026-10-24 under Python 3.14.6 / fastapi 0.141.1: 276 registry entries and
#: 2567 backend routes; 53 SLM source files parsed. Each floor leaves ~20% headroom.
BACKEND_REGISTRATIONS_FLOOR = 220
BACKEND_ROUTES_FLOOR = 2000
SLM_PARSED_FLOOR = 42
KNOWN_DUPLICATES = {
    ("POST", "/api/voice/realtime/tools/call"): "#18721",
    ("GET", "/api/llc/agent/context/{item_id}"): "#18721",
}


#: app name -> source files the SLM read parsed (the reach of that sweep), filled by ``_read``.
_PARSED: dict[str, int] = {}
#: Registry entries ``load_core_routers()`` + ``load_optional_routers()`` returned (the backend's reach).
_REGISTRATIONS: list[int] = []


def registered_routes(entries: list[tuple[Any, str, Any, str]]) -> tuple[list[OrderedRoute], list[str]]:
    """``(routes, unrecoverable)`` for registry tuples ``(router, prefix, tags, name)``, mounted at ``/api``.

    ``app_factory.py`` does ``app.include_router(router, prefix=f"/api{prefix}")``; this applies the same prefix to
    ``effective_routes(router)``. A route whose full path could not be recovered is listed, never dropped.
    """
    routes: list[OrderedRoute] = []
    unrecoverable: list[str] = []
    for router, prefix, _tags, name in entries:
        for mounted in effective_routes(router):
            if not mounted.prefix_complete:
                unrecoverable.append(f"{name}:{mounted.path}")
                continue
            endpoint = getattr(mounted.route, "endpoint", None)
            site = f"{name}:{getattr(endpoint, '__module__', '?')}.{getattr(endpoint, '__qualname__', '?')}"
            for method in mounted.methods or {"WEBSOCKET"}:
                routes.append(OrderedRoute(method, f"/api{prefix}{mounted.path}", site))
    return routes, unrecoverable


def _backend_entries() -> list[tuple[Any, str, Any, str]]:
    """The same two loader calls ``app_factory.py`` makes, in the same order."""
    backend = str(_ROOT / _BACKEND)
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from initialization.router_registry.core_routers import load_core_routers
    from initialization.routers import load_optional_routers

    logging.disable(logging.INFO)  # the loaders log one line per router
    try:
        return [*load_core_routers(), *load_optional_routers()]
    finally:
        logging.disable(logging.NOTSET)


@functools.lru_cache(maxsize=4)
def _read(root: Path) -> dict[str, tuple[list[OrderedRoute], list[str]]]:
    entries = _backend_entries()
    _REGISTRATIONS[:] = [len(entries)]
    order = RouteOrder(root / _SLM)
    slm = slm_routes(order)  # parses lazily: the reach is only known after the read
    _PARSED[_SLM] = len(order.parsed)
    return {_BACKEND: registered_routes(entries), _SLM: (slm, list(order.unresolved))}


def duplicates(routes: list[OrderedRoute]) -> dict[tuple[str, str], list[str]]:
    """``(method, path)`` -> the registering sites, for every pair registered more than once.

    Counts occurrences, not distinct sites: the same router mounted twice has ONE site and two entries.
    """
    seen: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for route in routes:
        seen[(route.method, route.path)].append(route.site)
    return {key: sites for key, sites in seen.items() if len(sites) > 1}


@pytest.mark.parametrize("app", [_BACKEND, _SLM])
def test_no_route_is_registered_twice(app: str) -> None:
    routes, unresolved = _read(_ROOT)[app]
    assert not unresolved, f"{app}: could not read {unresolved[:5]} -- this guard has NOT checked them"
    new = {k: v for k, v in duplicates(routes).items() if k not in KNOWN_DUPLICATES}
    assert not new, "\n".join(f"{m} {p} registered by {', '.join(sites)}" for (m, p), sites in sorted(new.items()))


def test_the_allowlist_only_lists_duplicates_that_still_exist() -> None:
    """A stale entry silently re-licenses the collision it named."""
    live = {key for routes, _ in _read(_ROOT).values() for key in duplicates(routes)}
    gone = set(KNOWN_DUPLICATES) - live
    assert not gone, f"these are fixed; remove them from KNOWN_DUPLICATES: {sorted(gone)}"


def test_the_allowlist_names_its_owning_issue() -> None:
    assert all(owner.startswith("#") for owner in KNOWN_DUPLICATES.values())


def test_the_reader_reached_both_apps() -> None:
    apps = _read(_ROOT)
    # Reach first, routes second: registry entries (backend) and source files parsed (SLM) say whether whole
    # modules silently stopped being read; the route counts stay as a second, independent instrument. A broken
    # import must not let the guard pass by finding nothing.
    assert _REGISTRATIONS[0] > BACKEND_REGISTRATIONS_FLOOR, _REGISTRATIONS
    assert _PARSED[_SLM] > SLM_PARSED_FLOOR, dict(_PARSED)
    assert len(apps[_BACKEND][0]) > BACKEND_ROUTES_FLOOR and len(apps[_SLM][0]) > 200, "the route read has drifted"


# -- the guard proved by plant --------------------------------------------------------------------------


def _r(method: str, path: str, site: str) -> OrderedRoute:
    return OrderedRoute(method, path, site)


def test_the_same_router_mounted_twice_is_caught() -> None:
    """The set-collapse blind spot: one site, two registrations, was one entry and so was invisible."""
    routes = [_r("GET", "/api/x", "api.x:10"), _r("GET", "/api/x", "api.x:10")]
    assert duplicates(routes) == {("GET", "/api/x"): ["api.x:10", "api.x:10"]}


def test_distinct_routes_and_methods_are_not_duplicates() -> None:
    routes = [_r("GET", "/api/x", "m:1"), _r("POST", "/api/x", "m:2"), _r("GET", "/api/y", "m:3")]
    assert duplicates(routes) == {}


def _planted_app(tmp_path: Path, main_body: str) -> list[OrderedRoute]:
    """A miniature SLM app: ``api.child`` has one route, ``api.parent`` composes it with include_router."""
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "api" / "child.py").write_text(
        'from fastapi import APIRouter\nrouter = APIRouter()\n@router.get("/dup")\ndef one(): ...\n',
        encoding="utf-8",
    )
    (tmp_path / "api" / "parent.py").write_text(
        "from fastapi import APIRouter\nfrom api.child import router as child_router\n"
        "router = APIRouter()\nrouter.include_router(child_router)\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "from api.child import router as child_router\nfrom api.parent import router as parent_router\n" + main_body,
        encoding="utf-8",
    )
    order = RouteOrder(tmp_path)
    routes = slm_routes(order)
    assert not order.unresolved
    return routes


def test_a_double_mount_through_include_router_is_caught(tmp_path: Path) -> None:
    """Contrast with the AST guard this replaces, which missed exactly this (#17325).

    ``parent`` already includes ``child``; ``main`` mounts ``child`` again directly.
    """
    routes = _planted_app(
        tmp_path, 'app.include_router(parent_router, prefix="/api")\napp.include_router(child_router, prefix="/api")\n'
    )
    assert duplicates(routes) == {("GET", "/api/dup"): ["api.child:3", "api.child:3"]}


def test_a_single_mount_through_include_router_is_clean(tmp_path: Path) -> None:
    routes = _planted_app(tmp_path, 'app.include_router(parent_router, prefix="/api")\n')
    assert [r.path for r in routes] == ["/api/dup"]
    assert duplicates(routes) == {}


def test_the_same_router_included_twice_into_one_parent_is_caught(tmp_path: Path) -> None:
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "api" / "child.py").write_text(
        'from fastapi import APIRouter\nrouter = APIRouter()\n@router.get("/dup")\ndef one(): ...\n', encoding="utf-8"
    )
    (tmp_path / "api" / "parent.py").write_text(
        "from fastapi import APIRouter\nfrom api.child import router as child_router\n"
        "router = APIRouter()\nrouter.include_router(child_router)\nrouter.include_router(child_router)\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        'from api.parent import router as parent_router\napp.include_router(parent_router, prefix="/api")\n',
        encoding="utf-8",
    )
    routes = slm_routes(RouteOrder(tmp_path))
    assert len(duplicates(routes)[("GET", "/api/dup")]) == 2


def test_a_duplicate_websocket_route_is_caught(tmp_path: Path) -> None:
    """The AST guard this replaced counted ``@router.websocket``; the route read must too."""
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "api" / "w.py").write_text(
        'from fastapi import APIRouter\nrouter = APIRouter()\n@router.websocket("/ws")\nasync def a(ws): ...\n'
        '@router.get("/ws")\ndef b(): ...\n',
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "from api.w import router as w_router\n"
        'app.include_router(w_router, prefix="/api")\napp.include_router(w_router, prefix="/api")\n',
        encoding="utf-8",
    )
    dupes = duplicates(slm_routes(RouteOrder(tmp_path)))
    assert set(dupes) == {("WEBSOCKET", "/api/ws"), ("GET", "/api/ws")}


# -- the real-router read proved by plant (#17325) -------------------------------------------------------


def _router_with(path: str, prefix: str = "") -> Any:
    from fastapi import APIRouter

    router = APIRouter(prefix=prefix)
    router.add_api_route(path, lambda: None, methods=["GET"])
    return router


def _nested(child: Any) -> Any:
    from fastapi import APIRouter

    parent = APIRouter()
    parent.include_router(child, prefix="/inner")
    return parent


def test_a_duplicate_inside_an_included_sub_router_is_caught() -> None:
    """The case the AST guard missed: one route reached through ``include_router`` with its own prefix."""
    flat = _router_with("/inner/dup")
    nested = _nested(_router_with("/dup"))
    routes, unrecoverable = registered_routes([(flat, "/x", [], "flat"), (nested, "/x", [], "nested")])
    assert not unrecoverable
    assert set(duplicates(routes)) == {("GET", "/api/x/inner/dup")}


def test_a_two_level_include_prefix_is_recovered_whole() -> None:
    routes, unrecoverable = registered_routes([(_nested(_nested(_router_with("/leaf", "/own"))), "/p", [], "deep")])
    assert not unrecoverable
    assert [r.path for r in routes] == ["/api/p/inner/inner/own/leaf"]


def test_the_same_router_registered_twice_by_the_registry_is_caught() -> None:
    router = _router_with("/dup")
    routes, _ = registered_routes([(router, "/x", [], "a"), (router, "/x", [], "a")])
    assert len(duplicates(routes)[("GET", "/api/x/dup")]) == 2
