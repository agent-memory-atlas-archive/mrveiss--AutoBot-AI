# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""No new blocking `KEYS <pattern>` in runtime code (#18999).

`<client>.keys(<args>)` is a Redis KEYS call (dict.keys() takes no arguments); it blocks the
server for the whole keyspace (48 ms observed). Existing sites are frozen in
`redis_keys_baseline_18999.py` and may only shrink.

Skipped directories are matched on the REPO-RELATIVE path parts (#18972), so a checkout living
under a directory called `tests` or `scripts` is still scanned.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from repo_tests._paths import repo_root
from repo_tests.redis_keys_baseline_18999 import BASELINE

ROOT = repo_root()
SKIP_PARTS = {
    "tests", "test", "scripts", "node_modules", "__pycache__", "site", "docs",
    "MagicMock", "debug", "data", "logs", ".git", ".venv", "venv",
}  # fmt: skip


def keys_call_lines(source: str) -> list[int]:
    """Lines of `<x>.keys(<arg>...)` calls: a call with arguments is Redis KEYS."""
    return sorted(
        n.lineno
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "keys"
        and (n.args or n.keywords)
    )


def is_runtime(rel: Path) -> bool:
    """Runtime source: no skipped directory part, not a test or conftest module."""
    if SKIP_PARTS & set(rel.parts[:-1]):
        return False
    name = rel.name
    return not (name.endswith("_test.py") or name.startswith("test_") or name == "conftest.py")


def scan(root: Path) -> dict[str, int]:
    """Repo-relative path -> number of KEYS sites, over runtime `.py` files under root."""
    found: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS and not d.startswith(".")]
        for fn in filenames:
            rel = rel_dir / fn
            if not fn.endswith(".py") or not is_runtime(rel):
                continue
            try:
                lines = keys_call_lines((root / rel).read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            if lines:
                found[rel.as_posix()] = len(lines)
    return found


def test_keys_sites_never_grow_and_stale_entries_fail():
    found = scan(ROOT)
    grown = {p: n for p, n in found.items() if n > BASELINE.get(p, 0)}
    stale = {p: n for p, n in BASELINE.items() if found.get(p, 0) < n}
    assert not grown, f"new blocking KEYS call(s); use autobot_shared.redis_scan: {grown}"
    assert not stale, f"baseline entry exceeds the tree; lower or remove it: {stale}"


# --- contrast fixtures: the detector, on strings -------------------------------------------


def test_detector_flags_redis_keys_with_pattern():
    assert keys_call_lines('r.keys("a:*")\nawait self.c.keys(f"{p}*")\nr.keys(pattern=x)') == [
        1,
        2,
        3,
    ]


def test_detector_ignores_dict_keys_and_scan():
    assert keys_call_lines("d.keys()\nfor k in r.scan_iter(match='a:*'): pass") == []


def test_skip_dirs_match_repo_relative_parts_not_absolute():
    # An absolute checkout path containing `tests`/`scripts` must not hide a runtime file (#18972).
    assert is_runtime(Path("autobot-backend/x/y.py"))
    assert not is_runtime(Path("autobot-backend/tests/y.py"))
    assert not is_runtime(Path("autobot-backend/x/y_test.py"))
    assert not is_runtime(Path("autobot-infrastructure/shared/scripts/y.py"))
