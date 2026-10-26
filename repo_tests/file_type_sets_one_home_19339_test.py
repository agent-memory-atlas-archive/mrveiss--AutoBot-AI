# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""File-type extension sets and skip-dir sets have ONE home (#19339).

``utils/file_categorization.py`` owns ``HTML_EXTENSIONS``, ``ALL_CODE_EXTENSIONS`` and ``SKIP_DIRS``;
``utils/file_types.py`` owns ``TEXT_FILE_EXTENSIONS`` and ``DOCX_EXTENSIONS`` (the first file is over the
size ceiling and may not grow). Before this guard the same concept was spelled out as a literal in three
text-extension copies, three HTML copies, two DOCX copies and ten skip-dir lists that had drifted apart --
``.doc`` passed one DOCX gate and then failed inside python-docx; ``.worktrees`` was skipped by only some
scanners. A scanner that needs more than the canonical set writes ``SKIP_DIRS | {"its", "own"}``.

Flagged, in any non-test module under ``autobot-backend``: an assignment (plain, annotated or attribute)
to a name that looks like one of these sets, whose value is a literal set/frozenset/tuple/list of two or
more strings, or a ``set``/``frozenset``/``tuple``/``list`` call over such a literal.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare

_ROOT = "autobot-backend"
_HOMES = frozenset({"autobot-backend/utils/file_categorization.py", "autobot-backend/utils/file_types.py"})

#: ``{path: "reason + issue"}`` for a reader that cannot import the home. Empty: every reader converted.
ALLOWED_LITERALS: dict[str, str] = {}

_NAME = re.compile(
    r"(?i)(_?(TEXT|HTML|DOCX|SUPPORTED)_?(FILE_)?EXTENSIONS|_?SKIP_DIR(S|ECTORIES)|_?EXCLUDED_DIRS|_?COCHANGE_SKIP_DIRS)$"
)
_WRAPPERS = frozenset({"set", "frozenset", "tuple", "list"})


def _modules(root: Path) -> list[Path]:
    return sorted(
        p
        for p in (root / _ROOT).rglob("*.py")
        if not {"tests", "__pycache__", "venv", ".venv", "node_modules"} & set(p.parts)
        and not p.name.endswith("_test.py")
        and not p.name.startswith("test_")
        and p.name != "conftest.py"
    )


#: 2,701 non-test backend modules measured at #19339; repinned by scripts/repin_reach_floors.py.
REACH = declare(
    "file-type-sets-one-home-19339",
    discover=_modules,
    floor=2566,  # mid-window: population 2701, window (2431, 2701)
    growth=270,  # ~10%: ordinary backend growth between repins must not turn an unrelated PR red
    what="non-test backend modules",
    roots=(_ROOT,),
)


def _target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Tuple | ast.List):
        return [n for element in target.elts for n in _target_names(element)]
    if isinstance(target, ast.Name):
        return [target.id]
    return [target.attr] if isinstance(target, ast.Attribute) else []


def _is_string_literal_collection(node: ast.AST | None) -> bool:
    """A set/tuple/list literal of 2+ string constants, directly or inside ``frozenset(...)`` and kin."""
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") in _WRAPPERS and len(node.args) == 1:
        node = node.args[0]
    if not isinstance(node, ast.Set | ast.Tuple | ast.List):
        return False
    strings = [e for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return len(strings) >= 2


def literal_copies(source: str, rel: str) -> list[str]:
    """Each re-declared extension/skip-dir literal in *source*, as ``path:line: NAME``."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [n for t in targets for n in _target_names(t) if _NAME.search(n)]
        if names and _is_string_literal_collection(node.value):
            hits.append(f"{rel}:{node.lineno}: {names[0]}")
    return hits


def test_extension_and_skip_dir_sets_have_one_home_on_the_live_tree() -> None:
    root = repo_root()
    files = REACH.examined(root)
    found: list[str] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if rel in _HOMES or rel in ALLOWED_LITERALS:
            continue
        found.extend(literal_copies(path.read_text(encoding="utf-8"), rel))
    REACH.completed(files)
    assert not found, (
        "import TEXT_FILE_EXTENSIONS/DOCX_EXTENSIONS from utils.file_types, or HTML_EXTENSIONS/SKIP_DIRS from "
        f"utils.file_categorization (extend with `SKIP_DIRS | {{...}}`), instead of re-declaring: {found}"
    )


def test_the_allowlist_names_only_modules_that_still_exist_and_say_why() -> None:
    root = repo_root()
    for rel, reason in ALLOWED_LITERALS.items():
        assert (root / rel).is_file(), f"{rel} is allowlisted but gone; remove the entry"
        assert re.search(r"#\d+", reason), f"{rel}: the allowlist reason must cite an issue"


def test_the_homes_exist_and_export_the_canonical_names() -> None:
    root = repo_root()
    names = {
        "autobot-backend/utils/file_categorization.py": {"HTML_EXTENSIONS", "ALL_CODE_EXTENSIONS", "SKIP_DIRS"},
        "autobot-backend/utils/file_types.py": {"TEXT_FILE_EXTENSIONS", "DOCX_EXTENSIONS"},
    }
    for rel, expected in names.items():
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        bound = {n for node in tree.body for t in _targets(node) for n in _target_names(t)}
        assert expected <= bound, f"{rel} no longer binds {sorted(expected - bound)}"


def _targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    return [node.target] if isinstance(node, ast.AnnAssign) else []


@pytest.mark.parametrize(
    "source",
    [
        "_TEXT_EXTENSIONS = {'.md', '.txt'}\n",
        "_HTML_EXTENSIONS = ('.html', '.htm')\n",
        "DOCX_EXTENSIONS = frozenset({'.docx', '.doc'})\n",
        "SUPPORTED_EXTENSIONS: set = set(['.pdf', '.txt'])\n",
        "EXCLUDED_DIRS = {'node_modules', '.git'}\n",
        "_SKIP_DIRECTORIES = ['venv', 'dist']\n",
        "_COCHANGE_SKIP_DIRS: frozenset = frozenset({'vendor', 'dist'})\n",
        "self.skip_dirs = {'a', 'b'}\n",
        "def f():\n    skip_dirs = {'node_modules', 'dist'}\n",
    ],
)
def test_each_restated_literal_shape_is_flagged(source: str) -> None:
    assert literal_copies(source, "autobot-backend/x.py")


@pytest.mark.parametrize(
    "source",
    [
        "from utils.file_types import TEXT_FILE_EXTENSIONS\n",
        "_SKIP_DIRS = SKIP_DIRS | {'tests'}\n",
        "_SKIP_DIRS: frozenset = frozenset(SKIP_DIRS | {'tests', 'e2e'})\n",
        "_HTML_EXTENSIONS = tuple(sorted(HTML_EXTENSIONS))\n",
        "DOCX_EXTENSIONS = {'.docx'}\n",
        "OTHER_NAMES = {'node_modules', '.git'}\n",
    ],
)
def test_an_import_a_derivation_or_an_unrelated_name_is_not_flagged(source: str) -> None:
    flagged = literal_copies(source, "autobot-backend/x.py")
    # A one-element literal is a single value, not a re-declared set.
    assert not flagged
