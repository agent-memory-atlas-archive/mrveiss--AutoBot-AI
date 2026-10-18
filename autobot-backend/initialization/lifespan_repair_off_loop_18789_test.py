# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""The startup HNSW pickle repair runs in a worker thread, never on the event loop (#18789).

`repair_hnsw_pickles_locked` waits up to REPAIR_LOCK_TIMEOUT_SECONDS for a flock and
fsyncs files, so a direct call from the async startup path stalls every request.
"""

import ast
from pathlib import Path

_LIFESPAN = Path(__file__).with_name("lifespan.py")
_REPAIR = "repair_hnsw_pickles_locked"


def _repair_call_sites(tree: ast.AST) -> tuple[list[int], list[int]]:
    """(lines calling it directly, lines handing it to asyncio.to_thread)."""
    direct, threaded = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == _REPAIR:
            direct.append(node.lineno)
        is_to_thread = isinstance(func, ast.Attribute) and func.attr == "to_thread"
        if is_to_thread and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == _REPAIR:
            threaded.append(node.lineno)
    return direct, threaded


def test_startup_repair_is_handed_to_a_worker_thread():
    direct, threaded = _repair_call_sites(ast.parse(_LIFESPAN.read_text(encoding="utf-8")))
    assert direct == [], f"{_REPAIR} called on the event loop at lifespan.py:{direct}"
    assert threaded, f"{_REPAIR} is no longer called from lifespan.py -- the #1390 post-index repair is gone"


def test_detector_contrast():
    blocking = f"async def f(p):\n    {_REPAIR}(p)\n"
    offloaded = f"import asyncio\nasync def f(p):\n    await asyncio.to_thread({_REPAIR}, p)\n"
    assert _repair_call_sites(ast.parse(blocking)) == ([2], [])
    assert _repair_call_sites(ast.parse(offloaded)) == ([], [3])
