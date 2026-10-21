#!/usr/bin/env python3
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Print the JS dependencies a push range ADDS, one `<name>\\t<manifest-dir>` per line (#19130).

Used by tools/git-hooks/pre-push: a dependency the changeset adds cannot be in the
linked node_modules (#16912), so a vitest failure on exactly that import is CI's to
verify. The names come from the package.json / package-lock.json diff itself
(head minus base), never from vitest's output.

Usage: added_js_deps.py <base-sha> <head-sha>
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

# Run with `python -I` (no implicit sys.path), so the repo root is added explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from autobot_shared.paths import scrubbed_git_env  # noqa: E402

_DEP_KEYS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
_MANIFESTS = ("package.json", "package-lock.json")


def _git(*args: str) -> str:
    done = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", check=False, env=scrubbed_git_env()
    )
    return done.stdout if done.returncode == 0 else ""


def _names(path: str, text: str) -> set[str]:
    """Dependency names declared by one manifest or lockfile blob ("" -> none)."""
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return set()
    if not isinstance(data, dict):
        return set()
    if PurePosixPath(path).name == "package.json":
        return {n for key in _DEP_KEYS for n in (data.get(key) or {})}
    return {k.rsplit("node_modules/", 1)[1] for k in (data.get("packages") or {}) if "node_modules/" in k}


def added_deps(base: str, head: str) -> list[tuple[str, str]]:
    """(name, manifest dir) pairs present at `head` and absent from `base`, sorted."""
    touched = _git("diff", "--name-only", "--diff-filter=AM", f"{base}..{head}").split()
    found: set[tuple[str, str]] = set()
    for path in touched:
        if PurePosixPath(path).name not in _MANIFESTS:
            continue
        added = _names(path, _git("show", f"{head}:{path}")) - _names(path, _git("show", f"{base}:{path}"))
        found.update((name, str(PurePosixPath(path).parent)) for name in added)
    return sorted(found)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write(f"{__doc__}\n")
        return 2
    for name, directory in added_deps(argv[1], argv[2]):
        sys.stdout.write(f"{name}\t{directory}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
