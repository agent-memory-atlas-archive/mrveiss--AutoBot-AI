# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Every backend name the ``shared/scripts/analysis`` tools import still exists (#18289).

Five of those tools imported names the backend had since removed (``circuit_breaker_manager``,
``conversation_manager``, ``chat_workflow.process_chat_message``, the ``development_speedup`` and
``npu_code_search`` module attributes). The class guard (#18288) only sees that a module imports a
backend PACKAGE, not that the name it takes from it is still there, and the lazy ones (inside a
function) fail only when the script is RUN. This resolves each ``from <backend module> import <name>``
at any depth, in a clean interpreter with the backend on the path, as the tool itself would.
"""

from __future__ import annotations

import ast
import functools
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from repo_tests._infra_backend_names import backend_only_names
from repo_tests._paths import repo_root

ANALYSIS = "autobot-infrastructure/shared/scripts/analysis"
#: The tools #18289 repaired or classified as backend tools in this folder.
TOOLS = (
    "debug_circuit_breaker",
    "demo_secure_web_research",
    "main",
    "simple_kb_test",
    "test_autobot_identity",
    "test_bypass_classification",
    "test_chat_debug",
    "test_chat_simple",
    "test_failsafe_sequence",
    "test_kb_documentation",
    "test_kb_search",
    "test_llm_config",
    "test_llm_direct",
    "test_npu_code_search",
    "test_redis_connection",
    "test_research_dialogue",
    "test_research_disabled",
    "test_web_research_security",
)

_RESOLVE = textwrap.dedent("""
    import importlib, json, sys
    root, wanted = sys.argv[1], json.loads(sys.argv[2])
    sys.path[:0] = [root + "/autobot-backend", root]
    cache, report = {}, {}
    for tool, imports in wanted.items():
        missing = []
        for module, names in imports.items():
            if module not in cache:
                try:
                    cache[module] = importlib.import_module(module)
                except Exception as exc:
                    cache[module] = f"{module}: {type(exc).__name__}: {exc}"
            if isinstance(cache[module], str):
                missing.append(cache[module])
                continue
            for name in names:
                if hasattr(cache[module], name):
                    continue
                try:
                    importlib.import_module(f"{module}.{name}")
                except Exception:
                    missing.append(f"{module}.{name}")
        report[tool] = missing
    print(json.dumps(report))
    """)


def backend_from_imports(source: str, backend: frozenset[str]) -> dict[str, list[str]]:
    """``{module: [names]}`` for every absolute ``from <backend module> import ...`` at any depth."""
    wanted: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] in backend:
                wanted.setdefault(node.module, set()).update(a.name for a in node.names if a.name != "*")
    return {module: sorted(names) for module, names in wanted.items()}


def unresolved(wanted: dict[str, dict[str, list[str]]], root: Path) -> dict[str, list[str]]:
    """Per label, the ``module.name`` strings that do not resolve (one interpreter for all, for speed)."""
    done = subprocess.run(  # noqa: S603 - fixed argv, our own interpreter
        [sys.executable, "-I", "-c", _RESOLVE, str(root), json.dumps(wanted)],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=240,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-800:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def _imports(tool: str) -> dict[str, list[str]]:
    source = (repo_root() / ANALYSIS / f"{tool}.py").read_text(encoding="utf-8")
    return backend_from_imports(source, backend_only_names())


@functools.lru_cache(maxsize=1)
def _report() -> dict[str, list[str]]:
    return unresolved({tool: _imports(tool) for tool in TOOLS}, repo_root())


@pytest.mark.parametrize("tool", TOOLS)
def test_every_backend_name_a_tool_imports_exists(tool: str) -> None:
    assert _imports(tool), f"{tool} imports nothing from the backend: it no longer belongs in this list"
    assert _report()[tool] == []


def test_contrast_a_removed_name_is_reported() -> None:
    """The pre-#18289 shape: a name the backend no longer has is named, not silently accepted."""
    wanted = backend_from_imports(
        "from circuit_breaker import circuit_breaker_manager, get_circuit_breaker_manager\n",
        backend_only_names(),
    )
    assert unresolved({"x": wanted}, repo_root()) == {"x": ["circuit_breaker.circuit_breaker_manager"]}
