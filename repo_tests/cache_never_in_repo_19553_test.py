# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A cache never resolves inside the repository (#19553, owner ruling 2026-10-25).

"If it can be regenerated it has no place in repo." The one home is
``autobot_shared.paths.cache_root()`` / ``shared_cache_path()``: ``AUTOBOT_CACHE_DIR``,
else the user cache, else a temp dir -- and a candidate inside a checkout is skipped.

The sweep reads every non-test Python module of the backends and ``autobot_shared``
for the two shapes that put a cache under the checkout: a relative ``data/...cache``
literal (resolved against the cwd, i.e. the code tree), and ``PATH.DATA_DIR`` /
``PATH.PROJECT_ROOT`` joined with a cache-named path. Contrast tests rebuild each
shape so a clean live run cannot come from a detector that lost its teeth.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare

from autobot_shared import paths

_ROOTS = ("autobot-backend", "autobot-slm-backend", "autobot_shared")
_TEST = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$|conftest\.py$)")
_CACHE_NAME = re.compile(r"cache|hashes\.json", re.IGNORECASE)
_RELATIVE_DATA = re.compile(r"^(\./)?data/")
_REPO_ANCHORS = frozenset({"DATA_DIR", "PROJECT_ROOT"})


def _modules(root: Path) -> list[Path]:
    found = []
    for top in _ROOTS:
        base = root / top
        if base.is_dir():
            found += [p for p in sorted(base.rglob("*.py")) if not _TEST.search(p.relative_to(root).as_posix())]
    return found


REACH = declare(
    "cache-never-in-repo",
    discover=_modules,
    floor=3278,  # mid-window: population 3278, window (3278, 3278)
    what="non-test backend and shared modules",
    roots=_ROOTS,
)


def _is_repo_anchor(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr in _REPO_ANCHORS


def repo_caches(source: str, rel: str) -> list[str]:
    """Each place *source* puts a cache under the checkout."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _RELATIVE_DATA.match(node.value) and _CACHE_NAME.search(node.value):
                found.append(f"{rel}:{node.lineno}: relative cache path {node.value!r}")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and _is_repo_anchor(node.left):
            right = node.right
            if isinstance(right, ast.Constant) and isinstance(right.value, str) and _CACHE_NAME.search(right.value):
                found.append(f"{rel}:{node.lineno}: {node.left.attr} / {right.value!r}")
    return found


def test_no_cache_resolves_inside_the_repo_on_the_live_tree() -> None:
    root = repo_root()
    files = REACH.examined(root)
    found = [hit for p in files for hit in repo_caches(p.read_text(encoding="utf-8"), p.relative_to(root).as_posix())]
    REACH.completed(files)
    assert (
        not found
    ), f"use autobot_shared.paths.shared_cache_path() -- a cache never goes in the repo (#19553): {found}"


def test_the_shared_cache_path_is_outside_this_checkout() -> None:
    assert not paths.shared_cache_path("probe").resolve().is_relative_to(repo_root().resolve())


@pytest.mark.parametrize(
    "source",
    [
        'CACHE = "data/cache/claude_responses"\n',
        'def f(cache_dir: str = "data/openvino_cache"): ...\n',
        'CACHE = PATH.DATA_DIR / ".code_index_hashes.json"\n',
        'CACHE = PATH.PROJECT_ROOT / "embeddings_cache"\n',
    ],
)
def test_each_repo_cache_shape_is_flagged(source: str) -> None:
    assert repo_caches(source, "x.py")


def test_data_that_is_not_a_cache_and_the_helper_are_not_flagged() -> None:
    assert not repo_caches('DB = PATH.DATA_DIR / "chromadb"\nC = shared_cache_path("x", "cache.json")\n', "x.py")


def test_a_cache_dir_inside_a_checkout_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True, env=paths.scrubbed_git_env())  # nosec B603 B607
    monkeypatch.setenv(paths.CACHE_DIR_ENV, str(checkout / "data" / "cache"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert paths.cache_root() == tmp_path / "xdg" / "autobot"


def test_an_operator_cache_dir_outside_any_checkout_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.CACHE_DIR_ENV, str(tmp_path / "var-cache"))
    assert paths.cache_root() == tmp_path / "var-cache"
