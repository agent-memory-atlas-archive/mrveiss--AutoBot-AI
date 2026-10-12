# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Issue #18414 / #18140: every method called on a memory graph (or memory
provider) receiver must exist on the real class.

A bare AsyncMock accepts any attribute, so a call to a method the graph does not
have passed its tests and raised AttributeError in production. This guard reads
the callers and checks them against ``dir()`` of the real class.
"""

import ast
from pathlib import Path

from autobot_memory_graph import AutoBotMemoryGraph
from services.memory.memory_provider_interface import MemoryProvider

BACKEND = Path(__file__).resolve().parents[2]
GRAPH_NAMES = {"memory_graph", "_memory_graph"}
GRAPH_FACTORIES = {"AutoBotMemoryGraph", "get_memory_graph", "_get_memory_graph_or_none", "resolve_memory_graph"}
PROVIDER_NAMES = {"built_in", "external"}
PROVIDER_FILES = {"memory/manager.py"}
SKIP_PARTS = {"node_modules", ".venv", "venv", "__pycache__"}


def _callee(node: ast.AST) -> str | None:
    """Name of the function being called, through an await."""
    if isinstance(node, ast.Await):
        node = node.value
    if isinstance(node, ast.Call):
        func = node.func
        return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    return None


def _graph_locals(tree: ast.AST) -> set[str]:
    """Local names bound from the graph class or a factory returning it."""
    names = set(GRAPH_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _callee(node.value) in GRAPH_FACTORIES:
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.arg) and node.annotation is not None:
            if "AutoBotMemoryGraph" in ast.unparse(node.annotation):
                names.add(node.arg)
    return names


def _receiver(node: ast.Attribute) -> str | None:
    base = node.value
    return base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", None)


def missing_calls(source: str, receivers: set[str], cls: type, include_locals: bool) -> list[tuple[str, int]]:
    """(method, line) for each `receiver.method(...)` call the class lacks."""
    tree = ast.parse(source)
    names = _graph_locals(tree) | receivers if include_locals else receivers
    have = set(dir(cls))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if _receiver(node.func) in names and node.func.attr not in have:
                found.append((node.func.attr, node.lineno))
    return found


def _production_files():
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if SKIP_PARTS & set(path.parts) or path.name.endswith("_test.py") or rel.startswith("tests/"):
            continue
        yield rel, path


def test_every_call_on_a_graph_receiver_exists():
    """Production callers only call methods AutoBotMemoryGraph has."""
    bad = []
    for rel, path in _production_files():
        text = path.read_text(encoding="utf-8")
        if "memory_graph" not in text and "AutoBotMemoryGraph" not in text:
            continue
        bad += [(rel, m, line) for m, line in missing_calls(text, GRAPH_NAMES, AutoBotMemoryGraph, True)]
    assert not bad, f"calls to methods AutoBotMemoryGraph does not have: {bad}"


def test_every_call_on_a_provider_receiver_exists():
    """The manager only calls methods the MemoryProvider interface declares."""
    bad = []
    for rel in PROVIDER_FILES:
        text = (BACKEND / rel).read_text(encoding="utf-8")
        bad += [(rel, m, line) for m, line in missing_calls(text, PROVIDER_NAMES, MemoryProvider, False)]
    assert not bad, f"calls to methods MemoryProvider does not have: {bad}"


def test_guard_flags_the_defects_it_exists_for():
    """Contrast: the pre-fix callers are flagged, so an empty result is not 'did not look'."""
    old = (
        "async def f(self):\n"
        "    await self._memory_graph.traverse_relations(entity_name='h', depth=2)\n"
        "    graph = AutoBotMemoryGraph()\n"
        "    await graph.add_or_update_entity(name='x')\n"
        "    await graph.add_relation(source='a', target='b')\n"
    )
    flagged = {m for m, _ in missing_calls(old, GRAPH_NAMES, AutoBotMemoryGraph, True)}
    assert flagged == {"traverse_relations", "add_or_update_entity", "add_relation"}
    ok = "async def f(self):\n    await self._memory_graph.get_related_entities('h')\n"
    assert missing_calls(ok, GRAPH_NAMES, AutoBotMemoryGraph, True) == []
