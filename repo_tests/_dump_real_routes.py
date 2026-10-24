# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Every route an AutoBot app really registers, read from the real router objects (#17325, #19033).

Run as ``python repo_tests/_dump_real_routes.py backend|slm`` it prints one JSON document on stdout:
``{"registrations": N, "routes": [[method, path, site], ...], "unrecoverable": [...]}``.

It is a script, not an import, because the two apps cannot share a process: both ship top-level ``api``,
``config`` and ``services`` packages. The caller (``no_duplicate_route_registration_16908_test.py``) runs it
once per app in a child interpreter, so importing a backend leaves nothing behind in the test process.

* backend: the two loader calls ``app_factory.py`` mounts from, at ``/api{prefix}`` as it mounts them.
* slm: the ``app`` ``main.py`` builds, whose ``include_router`` calls run at import.

Both are walked with ``autobot_shared.api_routing.router_routes.effective_routes``, the one route traversal.
Design from commit ``f4e2d6badb`` (#16908), which built the backend half and never landed.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]


def registered_routes(entries: list[tuple[Any, str, Any, str]]) -> tuple[list[list[str]], list[str]]:
    """``(routes, unrecoverable)`` for registry tuples ``(router, prefix, tags, name)``, mounted at ``/api``.

    ``app_factory.py`` does ``app.include_router(router, prefix=f"/api{prefix}")``; this applies the same prefix to
    ``effective_routes(router)``. A route whose full path could not be recovered is listed, never dropped.
    """
    from autobot_shared.api_routing.router_routes import effective_routes

    routes: list[list[str]] = []
    unrecoverable: list[str] = []
    for router, prefix, _tags, name in entries:
        for mounted in effective_routes(router):
            if not mounted.prefix_complete:
                unrecoverable.append(f"{name}:{mounted.path}")
                continue
            routes.extend(_rows(mounted, f"/api{prefix}{mounted.path}", name))
    return routes, unrecoverable


def _rows(mounted: Any, full_path: str, name: str) -> list[list[str]]:
    endpoint = getattr(mounted.route, "endpoint", None)
    site = f"{name}:{getattr(endpoint, '__module__', '?')}.{getattr(endpoint, '__qualname__', '?')}"
    return [[method, full_path, site] for method in sorted(mounted.methods or {"WEBSOCKET"})]


def _dump_backend() -> dict[str, Any]:
    sys.path.insert(0, str(_ROOT / "autobot-backend"))
    from initialization.router_registry.core_routers import load_core_routers
    from initialization.routers import load_optional_routers

    entries = [*load_core_routers(), *load_optional_routers()]
    routes, unrecoverable = registered_routes(entries)
    return {"registrations": len(entries), "routes": routes, "unrecoverable": unrecoverable}


def _dump_slm() -> dict[str, Any]:
    from autobot_shared.api_routing.router_routes import effective_routes

    # Throwaway keys: config.py would otherwise write data/.slm_keys into the checkout at import.
    os.environ.setdefault("SLM_SECRET_KEY", "route-dump-not-a-secret")
    os.environ.setdefault("SLM_ENCRYPTION_KEY", "route-dump-not-a-secret")
    os.chdir(_ROOT / "autobot-slm-backend")
    sys.path.insert(0, os.getcwd())
    import main

    routes: list[list[str]] = []
    unrecoverable: list[str] = []
    for mounted in effective_routes(main.app.router):
        if mounted.prefix_complete:
            routes.extend(_rows(mounted, mounted.path, "slm"))
        else:
            unrecoverable.append(mounted.path)
    return {"registrations": len(main.app.router.routes), "routes": routes, "unrecoverable": unrecoverable}


def main_cli(argv: list[str]) -> int:
    sys.path.insert(0, str(_ROOT))
    logging.disable(logging.CRITICAL)
    dump = {"backend": _dump_backend, "slm": _dump_slm}[argv[1]]()
    sys.stdout.write("\n" + json.dumps(dump) + "\n")  # the last line is the document; imports may print before it
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv))
