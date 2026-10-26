# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The knowledge-base populate script has ONE home (#19339, maintainer decision 2026-10-26).

``autobot-infrastructure/shared/scripts/populate_knowledge_base.py`` adds the project documentation
through the KB layer; ``--patterns`` covers the docs-only variant. Four copies were retired:
``populate_kb_fixed.py`` (the same flow plus an env var nothing reads), ``populate_kb_chromadb.py``
(raw writes into the KB's Chroma store, around the KB layer), ``direct_kb_populate.py`` and
``populate_docs_knowledge.py``. ``simple_populate.py`` (online, through the backend API) and
``sync_kb_docs.py`` (incremental sync) keep their own jobs but import the one documentation set.

A copy shows up as its documentation set: this guard fails when any non-test infrastructure module
other than the home spells out a pattern list holding ``docs/**/*.md``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare

_ROOT = repo_root()
_HOME = "autobot-infrastructure/shared/scripts/populate_knowledge_base.py"
_ROOTS = ("autobot-infrastructure",)
_MARKER = "docs/**/*.md"


def _sources(root: Path) -> list[Path]:
    base = root / _ROOTS[0]
    if not base.is_dir():
        return []
    return [p for p in sorted(base.rglob("*.py")) if not p.name.endswith("_test.py") and not p.name.startswith("test_")]


REACH = declare(
    "kb-populate-doc-patterns",
    discover=_sources,
    floor=180,  # mid-window: population 189, window (170, 189)
    growth=19,  # ~10%: ordinary growth between repins must not turn an unrelated PR red
    what="non-test infrastructure Python modules",
    roots=_ROOTS,
)


def pattern_lists(source: str, rel: str) -> list[str]:
    """Each list/tuple/set literal in *source* that holds the documentation glob."""
    if rel == _HOME:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [
        f"{rel}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.List | ast.Tuple | ast.Set)
        and any(isinstance(e, ast.Constant) and e.value == _MARKER for e in node.elts)
    ]


def test_no_module_restates_the_documentation_set() -> None:
    files = REACH.examined(_ROOT)
    found = [h for p in files for h in pattern_lists(p.read_text(encoding="utf-8"), p.relative_to(_ROOT).as_posix())]
    REACH.completed(files)
    assert not found, f"import DOC_PATTERNS / collect_documentation_files from {_HOME} (#19339): {found}"


@pytest.mark.parametrize(
    "source",
    ['doc_patterns = ["README.md", "CLAUDE.md", "docs/**/*.md"]\n', 'for p in ("docs/**/*.md",):\n    pass\n'],
)
def test_a_restated_documentation_set_is_flagged(source: str) -> None:
    assert pattern_lists(source, "x.py")


def test_the_home_and_an_import_are_not_flagged() -> None:
    assert not pattern_lists('DOC_PATTERNS = ["docs/**/*.md"]\n', _HOME)
    assert not pattern_lists("from populate_knowledge_base import DOC_PATTERNS\n", "x.py")
