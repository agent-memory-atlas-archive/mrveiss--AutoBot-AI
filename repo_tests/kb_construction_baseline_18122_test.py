#!/usr/bin/env python3
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""#18122 (S3) -- independent `KnowledgeBase(` constructions, asserted and shrink-only.

`knowledge_factory` is the one creator: it builds the instance, initialises it and
publishes it to both stores. Every other `KnowledgeBase()` is a second instance that is
never published, frequently never initialised, and invisible to `peek_knowledge_base()`.
This pins the current population so a new one cannot appear quietly and so draining one is
a deliberate edit here. The baseline only shrinks (slices S4..n drain it).

Matching is by AST on call nodes whose callee resolves to the class defined in
`knowledge/_composed.py` (exported by `knowledge`, re-exported by `knowledge_base`). A
docstring mention, `MockKnowledgeBase`, and a same-named class from another module are not
that class. Decision on the last: a locally defined `class KnowledgeBase` outside the
defining module shadows the name, so its bare calls do NOT count.
"""

from __future__ import annotations

import ast
import functools
from typing import NamedTuple

import pytest
from repo_tests._paths import repo_root

from tools.lint._scan_helpers import tracked_paths

CANONICAL_CREATOR = "autobot-backend/knowledge_factory.py"
#: Where the class is defined; inside it the bare name is the class itself.
DEFINER = "autobot-backend/knowledge/_composed.py"
#: Modules a caller imports the class from (matched on the dotted suffix, so relative
#: and package-prefixed forms resolve too).
_SOURCES = ("knowledge", "knowledge._composed", "knowledge_base")
_SEARCH_ROOTS = ("autobot-backend/",)
_TEST_DIRS = frozenset({"tests", "testing", "testkit"})

_PERM = "PERMANENT (D6)"
_DRAIN = "drain"
#: path -> (count, reason). SHRINK-ONLY. Reasons starting "PERMANENT" are owner-decided
#: exceptions that never drain; every other entry is debt for a later slice.
BASELINE: dict[str, tuple[int, str]] = {
    "autobot-backend/tasks/knowledge_tasks.py": (
        1,
        f"{_PERM}: builds its KB inside its own event loop in a worker process; cannot share the app instance",
    ),
    "autobot-backend/api/knowledge_debug.py": (2, f"{_DRAIN} (D5): use the canonical instance"),
    "autobot-backend/api/knowledge_eval.py": (2, f"{_DRAIN} (D5): use the canonical instance"),
    "autobot-backend/knowledge/_composed.py": (1, f"{_DRAIN} (S2): becomes a facade over knowledge_factory"),
    "autobot-backend/utils/system_validator.py": (2, f"{_DRAIN}: validator builds a private KB"),
    "autobot-backend/agents/knowledge_retrieval_agent.py": (1, f"{_DRAIN}: agent builds a private KB"),
    "autobot-backend/agents/kb_librarian_agent.py": (1, f"{_DRAIN}: agent builds a private KB"),
    "autobot-backend/agents/librarian_assistant.py": (1, f"{_DRAIN}: agent builds a private KB"),
    "autobot-backend/knowledge_sync_incremental.py": (1, f"{_DRAIN}: sync builds a private KB"),
    "autobot-backend/api/chat_knowledge_manager.py": (1, f"{_DRAIN}: manager builds a private KB"),
    "autobot-backend/worker_node.py": (1, f"{_DRAIN}: worker builds a private KB"),
    "autobot-backend/orchestrator.py": (1, f"{_DRAIN}: orchestrator builds a private KB"),
    "autobot-backend/npu_semantic_search.py": (1, f"{_DRAIN}: search builds a private KB"),
    "autobot-backend/api/workflow.py": (1, f"{_DRAIN}: route builds a private KB"),
    "autobot-backend/utils/operation_timeout_integration.py": (1, f"{_DRAIN}: helper builds a private KB"),
    "autobot-backend/api/intelligent_agent.py": (1, f"{_DRAIN}: route builds a private KB"),
    "autobot-backend/utils/error_boundary_examples.py": (1, f"{_DRAIN}: example code builds a private KB"),
    "autobot-backend/services/advanced_workflow/coordinator.py": (1, f"{_DRAIN}: coordinator builds a private KB"),
}


@functools.lru_cache(maxsize=1)
def _tracked_python_files() -> tuple[str, ...]:
    # #15926: `tracked_paths` is the one git enumerator and raises on an empty result.
    return tuple(tracked_paths(repo_root(), "*.py"))


def _is_test_path(rel: str) -> bool:
    """Decided on REPO-RELATIVE parts only (#18972: absolute paths blind worktrees)."""
    *dirs, name = rel.split("/")
    return (
        name.endswith("_test.py") or name.startswith("test_") or name == "conftest.py" or bool(_TEST_DIRS & set(dirs))
    )


def _from_source(module: str | None) -> bool:
    mod = (module or "").lstrip(".")
    return any(mod == s or mod.endswith("." + s) for s in _SOURCES)


def _bindings(tree: ast.Module, defines_it: bool) -> tuple[set[str], set[str]]:
    """(names bound to the class, names bound to a module that exports it)."""
    classes: set[str] = {"KnowledgeBase"} if defines_it else set()
    modules: set[str] = {"knowledge", "knowledge_base"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "KnowledgeBase" and _from_source(node.module):
                    classes.add(a.asname or a.name)
                elif a.name in _SOURCES and not (node.module or "").lstrip("."):
                    modules.add(a.asname or a.name)  # `from . import knowledge_base`
                elif _from_source(f"{(node.module or '').lstrip('.')}.{a.name}") and a.name in _SOURCES:
                    modules.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            modules |= {a.asname or a.name.split(".")[-1] for a in node.names if _from_source(a.name)}
        elif isinstance(node, ast.ClassDef) and node.name == "KnowledgeBase" and not defines_it:
            classes.discard("KnowledgeBase")  # a same-named class from elsewhere shadows it
    return classes, modules


def _construction_lines(source: str, rel: str = "") -> list[int]:
    """Line numbers of calls that construct the real KnowledgeBase."""
    tree = ast.parse(source)
    classes, modules = _bindings(tree, rel == DEFINER)
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in classes:
            lines.append(node.lineno)
        elif isinstance(f, ast.Attribute) and f.attr == "KnowledgeBase":
            tail = f.value.id if isinstance(f.value, ast.Name) else getattr(f.value, "attr", None)
            if tail in modules:
                lines.append(node.lineno)
    return sorted(lines)


class _Scan(NamedTuple):
    sites: dict[str, tuple[int, ...]]
    files_parsed: int
    nodes: int
    unparsable: tuple[str, ...]


@functools.lru_cache(maxsize=1)
def _scan() -> _Scan:
    root = repo_root()
    sites: dict[str, tuple[int, ...]] = {}
    bad: list[str] = []
    parsed = nodes = 0
    for rel in _tracked_python_files():
        if not rel.startswith(_SEARCH_ROOTS) or _is_test_path(rel):
            continue
        try:
            source = (root / rel).read_text(encoding="utf-8")
            nodes += sum(1 for _ in ast.walk(ast.parse(source)))
            found = _construction_lines(source, rel)
        except (OSError, SyntaxError) as exc:
            bad.append(f"{rel}: {type(exc).__name__}")
            continue
        parsed += 1
        if found:
            sites[rel] = tuple(found)
    return _Scan(sites, parsed, nodes, tuple(bad))


def _live() -> dict[str, tuple[int, ...]]:
    return {k: v for k, v in _scan().sites.items() if k != CANONICAL_CREATOR}


def test_no_new_independent_knowledge_base_construction() -> None:
    over = []
    for rel, lines in sorted(_live().items()):
        allowed = BASELINE.get(rel, (0, ""))[0]
        if len(lines) > allowed:
            over.append(f"{rel}:{','.join(map(str, lines))} ({len(lines)} found, {allowed} allowed)")
    assert not over, (
        f"#18122: independent KnowledgeBase() construction: {over}. Use "
        "`knowledge_factory.get_knowledge_base_async()` (app-free) or "
        "`knowledge_factory.get_or_create_knowledge_base(app)` (with a request); a second "
        "instance is never published and may never be initialised."
    )


def test_baseline_only_shrinks() -> None:
    """A baseline entry above its live count must be lowered or removed."""
    live = _live()
    stale = [
        f"{rel}: baseline {n}, live {len(live.get(rel, ()))}"
        for rel, (n, _) in sorted(BASELINE.items())
        if len(live.get(rel, ())) < n
    ]
    assert not stale, f"#18122: drained but still baselined -- lower or delete the entry: {stale}"


def test_baseline_entries_carry_a_reason_and_the_creator_is_not_listed() -> None:
    assert CANONICAL_CREATOR not in BASELINE, "the canonical creator is always allowed, never baselined"
    assert all(n > 0 and reason for n, reason in BASELINE.values())
    permanent = [r for r, (_, why) in BASELINE.items() if why.startswith("PERMANENT")]
    assert permanent == ["autobot-backend/tasks/knowledge_tasks.py"], "permanent exceptions need an owner decision"


_IMPORT = "from knowledge_base import KnowledgeBase\n"


@pytest.mark.parametrize(
    ("label", "source", "rel", "expected"),
    [
        ("bare call", _IMPORT + "kb = KnowledgeBase()\n", "", 1),
        ("package import", "from knowledge import KnowledgeBase\nKnowledgeBase()\n", "", 1),
        ("relative import", "from ..knowledge_base import KnowledgeBase\nKnowledgeBase()\n", "", 1),
        ("aliased import", "from knowledge import KnowledgeBase as KB\nKB()\n", "", 1),
        ("attribute form", "import knowledge_base\nknowledge_base.KnowledgeBase()\n", "", 1),
        ("attribute on package", "import knowledge\nknowledge.KnowledgeBase(x=1)\n", "", 1),
        ("module alias", "import knowledge_base as kbm\nkbm.KnowledgeBase()\n", "", 1),
        ("two calls", _IMPORT + "KnowledgeBase()\nKnowledgeBase()\n", "", 2),
        ("defining module", "class KnowledgeBase: pass\nKnowledgeBase()\n", DEFINER, 1),
        ("docstring mention", _IMPORT + '"""KnowledgeBase() is built elsewhere."""\n# KnowledgeBase()\n', "", 0),
        ("Mock class", "kb = MockKnowledgeBase()\nFakeKnowledgeBase()\n", "", 0),
        ("reference only", _IMPORT + "def f(kb: KnowledgeBase) -> None: ...\n", "", 0),
        ("same name, other module", "from other.store import KnowledgeBase\nKnowledgeBase()\n", "", 0),
        ("same name, local class", _IMPORT + "class KnowledgeBase: ...\nKnowledgeBase()\n", "", 0),
        ("unrelated attribute", "import other\nother.KnowledgeBase()\n", "", 0),
    ],
)
def test_matcher_contrast_fixtures(label: str, source: str, rel: str, expected: int) -> None:
    assert len(_construction_lines(source, rel)) == expected, label


@pytest.mark.parametrize(
    ("rel", "is_test"),
    [
        ("autobot-backend/x_test.py", True),
        ("autobot-backend/test_x.py", True),
        ("autobot-backend/tests/helper.py", True),
        ("autobot-backend/knowledge/connectors/testing/h.py", True),
        ("autobot-backend/pkg/testkit/h.py", True),
        ("autobot-backend/conftest.py", True),
        ("autobot-backend/api/workflow.py", False),
        ("autobot-backend/contests/real.py", False),
    ],
)
def test_test_files_are_excluded(rel: str, is_test: bool) -> None:
    assert _is_test_path(rel) is is_test, rel


def test_the_scan_is_not_vacuous() -> None:
    """Floors bind to REACH (files, AST nodes), not to findings; parts are repo-relative."""
    scan = _scan()
    assert len(_tracked_python_files()) > 3000, "git ls-files returned almost nothing"
    assert scan.files_parsed > 500, f"only {scan.files_parsed} files parsed -- the roots are wrong"
    assert scan.nodes > 50_000, f"only {scan.nodes} AST nodes inspected"
    assert not scan.unparsable, f"lost reach, {len(scan.unparsable)} file(s): {scan.unparsable[:3]}"
    assert CANONICAL_CREATOR in scan.sites, "the matcher no longer sees the canonical creator -- it is broken"
