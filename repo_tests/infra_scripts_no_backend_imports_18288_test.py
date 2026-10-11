# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""No infrastructure module imports a backend-only package unless it is a recorded backend tool (#18288).

Infrastructure scripts imported ``utils.*`` and friends from ``autobot-backend/`` and resolved them only
when the CALLER's PYTHONPATH already held the backend; run on their own they died with
``ModuleNotFoundError``. #18287 fixed one (``monitor_services.py``); #18288 is the class.

**Why a syntax-tree check and not an import of every file.** ~280 modules, many of which start
services, open Redis or walk the tree at import -- importing them all is slow and has side effects. The
tree check also sees imports inside functions, which an import cannot: ``test_phase5_cleanup.py`` and
``analysis/test_service_registry.py`` imported cleanly before #18288 and failed only when RUN.

**The backend-only set is derived, not listed**: every top-level module or package under
``autobot-backend/``, minus the standard library and minus any name the repository root also provides
(``tools``, ``scripts``, ...), because an infra import of those may mean the root's. A name the
importing file's own directory chain provides is its own tree, not the backend's.

An infra module that legitimately drives the backend is a backend TOOL. It is listed in
``infra_backend_imports_allowlist_18288`` with its reason, and the audited ones must also import in a
clean interpreter (``python -I``, no PYTHONPATH, cwd outside the repo): the tool puts the backend on its
own path, which is the documented entry point the issue asks for.
"""

from __future__ import annotations

import ast
import functools
import os
import subprocess
import sys
import textwrap
from pathlib import Path, PurePosixPath

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare
from repo_tests.infra_backend_imports_allowlist_18288 import ALLOWLIST, BACKEND_TOOLS

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

SCAN_ROOTS = ("autobot-infrastructure",)
BACKEND_ROOT = "autobot-backend"
#: The backend has well over two hundred top-level names; a derivation that found few broke.
MIN_BACKEND_NAMES = 150


def _tracked_py(root: Path, *trees: str) -> list[str]:
    try:
        return [rel for rel in tracked_paths(root, *trees) if rel.endswith(".py")]
    except EmptyEnumeration:
        return []


def _population(root: Path) -> list[str]:
    return _tracked_py(root, *SCAN_ROOTS)


REACH = declare(
    "infra-backend-import-scan",
    discover=_population,
    # Mid-window from REACH.window(): population 281, skips 0, growth 30 -> (251, 281, 266).
    floor=266,
    what="python modules",
    roots=SCAN_ROOTS,
    growth=30,
)


def _top_level_names(paths: list[str], prefix: str) -> set[str]:
    """First path component under *prefix* of each python file: a module stem or a package dir."""
    names = set()
    for rel in paths:
        parts = PurePosixPath(rel).relative_to(prefix).parts if prefix else PurePosixPath(rel).parts
        names.add(parts[0].removesuffix(".py"))
    return names


@functools.lru_cache(maxsize=1)
def backend_only_names() -> frozenset[str]:
    """Top-level backend names an infra module cannot resolve without the backend on its path."""
    root = repo_root()
    backend = _top_level_names(_tracked_py(root, BACKEND_ROOT), BACKEND_ROOT)
    return frozenset(backend - _root_names(root) - set(sys.stdlib_module_names) - {"__init__"})


def _root_names(root: Path) -> set[str]:
    """Modules and python-bearing directories at the repository root: what its own path provides."""
    packages = {d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith(".") and any(d.glob("*.py"))}
    return packages | {p.stem for p in root.glob("*.py")}


def _imported_tops(source: str) -> set[str]:
    """Top-level names of every absolute import, at any depth -- function bodies included."""
    tops = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            tops.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            tops.add(node.module.split(".")[0])
    return tops


def _own_tree_names(rel: str, tracked: frozenset[str]) -> set[str]:
    """Names the importing file's own directory chain provides, up to the infrastructure root."""
    names = set()
    for parent in PurePosixPath(rel).parents:
        if str(parent) in (".", ""):
            break
        prefix = f"{parent}/"
        names |= _top_level_names([t for t in tracked if t.startswith(prefix)], str(parent))
    return names


def backend_imports(source: str, own: set[str], backend: frozenset[str]) -> set[str]:
    """The backend-only packages *source* imports that its own tree does not provide."""
    return (_imported_tops(source) & backend) - own


@functools.lru_cache(maxsize=1)
def _offenders() -> tuple[dict[str, tuple[str, ...]], int]:
    root = repo_root()
    examined = list(REACH.examined(root))
    tracked = frozenset(examined)
    found: dict[str, tuple[str, ...]] = {}
    read = 0
    for rel in examined:
        source = (root / rel).read_text(encoding="utf-8")
        hits = backend_imports(source, _own_tree_names(rel, tracked), backend_only_names())
        read += 1
        if hits:
            found[rel] = tuple(sorted(hits))
    REACH.completed(read)
    return found, read


_BACKEND = frozenset({"utils", "api", "constants", "knowledge_base"})


@pytest.mark.parametrize(
    ("label", "source", "own", "expected"),
    [
        ("module-level backend import", "from utils.template_loader import load_css\n", set(), {"utils"}),
        ("lazy import inside a function", "def f():\n    from utils.x import y\n", set(), {"utils"}),
        ("plain dotted import", "import api.routes\n", set(), {"api"}),
        ("shared library", "from autobot_shared.paths import project_root\n", set(), set()),
        ("standard library", "import json\nfrom os import path\n", set(), set()),
        ("relative import", "from .utils import helper\n", set(), set()),
        ("own-tree sibling of the same name", "from constants import X\n", {"constants"}, set()),
        ("a comment naming it", "# from utils.x import y\nx = 1\n", set(), set()),
    ],
)
def test_the_detector_tells_a_backend_import_from_everything_else(label, source, own, expected) -> None:
    assert backend_imports(source, own, _BACKEND) == expected, label


def test_the_backend_set_is_derived_and_excludes_the_repo_roots_names() -> None:
    names = backend_only_names()
    assert len(names) >= MIN_BACKEND_NAMES, f"derived only {len(names)} backend-only names"
    assert {"utils", "constants", "api", "knowledge_base"} <= names
    assert not {"tools", "scripts", "autobot_shared", "json"} & names


def test_the_scan_is_not_vacuous() -> None:
    _, read = _offenders()
    assert read >= REACH.floor, f"read {read} infrastructure modules, below the declared floor {REACH.floor}"


def test_no_infrastructure_module_imports_the_backend_unless_recorded() -> None:
    found, _ = _offenders()
    new = {rel: hits for rel, hits in found.items() if rel not in ALLOWLIST}
    assert not new, (
        f"#18288: {new} import backend-only packages. Use autobot_shared (move genuinely shared code "
        "there, never copy), or make the script an explicit backend tool: bootstrap the backend onto its "
        "own path, add it to BACKEND_TOOLS in infra_backend_imports_allowlist_18288.py with its reason."
    )


def test_every_allowlist_entry_still_imports_the_backend_and_has_a_reason() -> None:
    found, _ = _offenders()
    stale = sorted(rel for rel in ALLOWLIST if rel not in found)
    assert not stale, f"no longer import the backend (or are gone) -- remove them from the allowlist: {stale}"
    assert all(reason.strip() for reason in ALLOWLIST.values())


_IMPORT = textwrap.dedent("""
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("backend_tool_under_test", sys.argv[1])
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
    print("IMPORTED")
    """)


def _clean_import(path: Path, cwd: Path) -> subprocess.CompletedProcess:
    """Import *path* as a module in an isolated interpreter: no PYTHONPATH, no user site, cwd outside."""
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(cwd)}
    return subprocess.run(  # noqa: S603 - fixed argv, our own interpreter
        [sys.executable, "-I", "-c", _IMPORT, str(path)],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=180,
        check=False,
    )


@pytest.mark.parametrize("rel", sorted(BACKEND_TOOLS))
def test_a_backend_tool_puts_the_backend_on_its_own_path(rel: str, tmp_path: Path) -> None:
    done = _clean_import(repo_root() / rel, tmp_path)
    assert done.returncode == 0 and "IMPORTED" in done.stdout, done.stderr[-800:]


def test_contrast_a_backend_import_without_the_bootstrap_fails_clean(tmp_path: Path) -> None:
    """The pre-#18288 shape: a backend import with no bootstrap dies in the same clean interpreter."""
    bare = tmp_path / "bare_tool.py"
    bare.write_text("from utils.template_loader import load_css\n", encoding="utf-8")
    done = _clean_import(bare, tmp_path)
    assert done.returncode != 0 and "ModuleNotFoundError" in done.stderr, done.stderr[-400:]
