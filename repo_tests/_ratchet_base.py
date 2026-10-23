# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""The base a change is ratcheted against, read fail-closed (#19283).

Down-only ratchets used hand-maintained totals (MAX_CEILING_TOTAL, TOTAL_OCCURRENCES,
css_rule_declarations) or a second mirrored table, so that raising an entry would show up
somewhere. Every PR that lowered a different entry also edited that one total line, and two
such PRs always conflicted. The tripwire here is the base commit instead: each entry must not
exceed what the same table said at the base, and an entry the base did not have is a raise.
Nothing is hand-summed, so PRs that lower different entries touch different lines.

The base is:
- in CI (``GITHUB_ACTIONS``): ``HEAD^1``. A pull_request run checks out the merge ref, whose
  first parent is the base tip; a push run's first parent is the previous base commit. The
  checkout needs ``fetch-depth: 2``.
- elsewhere: ``git merge-base HEAD <AUTOBOT_RATCHET_BASE_REF, default origin/main>``.

If the base cannot be read, :class:`BaseUnavailable` is raised. It is never treated as
"nothing rose": a ratchet that cannot see its base has proved nothing.
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

from autobot_shared.paths import scrubbed_git_env

BASE_REF_ENV = "AUTOBOT_RATCHET_BASE_REF"


class BaseUnavailable(RuntimeError):
    """The base commit (or a file at it) could not be read; the ratchet refuses to judge."""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60, env=scrubbed_git_env()
    )


def base_commit(root: Path) -> str:
    """The commit this tree is ratcheted against (see the module docstring)."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        out = _git(root, "rev-parse", "--verify", "--quiet", "HEAD^1")
        if out.returncode != 0:
            raise BaseUnavailable(
                "HEAD^1 is missing, so the ratchet base cannot be read. The CI checkout needs "
                "`fetch-depth: 2` (#19283); refusing rather than reporting 'nothing rose'."
            )
        return out.stdout.strip()
    ref = os.environ.get(BASE_REF_ENV, "origin/main")
    out = _git(root, "merge-base", "HEAD", ref)
    if out.returncode != 0:
        raise BaseUnavailable(
            f"`git merge-base HEAD {ref}` failed ({out.stderr.strip()}); fetch {ref} or set {BASE_REF_ENV}."
        )
    return out.stdout.strip()


def base_text(root: Path, rel: str) -> str | None:
    """*rel*'s content at the base, or None if the file did not exist there."""
    sha = base_commit(root)
    out = _git(root, "show", f"{sha}:{rel}")
    if out.returncode == 0:
        return out.stdout
    # git's two phrasings for "this path is not in that commit"; anything else is a read failure.
    if "does not exist in" in out.stderr or "exists on disk, but not in" in out.stderr:
        return None
    raise BaseUnavailable(f"cannot read {rel} at the base {sha[:10]}: {out.stderr.strip()}")


def literal_in(source: str, name: str) -> object:
    """The literal assigned to module-level *name* in *source* (``NAME = {...}`` or annotated)."""
    for node in ast.parse(source).body:
        targets = (
            node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == name for t in targets) and node.value is not None:
            return ast.literal_eval(node.value)
    raise BaseUnavailable(f"no literal {name} = ... in the base source")


def raised_entries(now: dict[str, int], base: dict[str, int]) -> dict[str, tuple[int | None, int]]:
    """Entries that rose against the base, ``{key: (base value or None if new, now)}``."""
    return {k: (base.get(k), v) for k, v in now.items() if k not in base or v > base[k]}


def base_tree_texts(root: Path, prefix: str) -> dict[str, str]:
    """``{path: text}`` for every file under *prefix* at the base, in two git calls."""
    sha = base_commit(root)
    listing = _git(root, "ls-tree", "-r", "-z", "--name-only", sha, "--", prefix)
    if listing.returncode != 0:
        raise BaseUnavailable(f"cannot list {prefix} at the base {sha[:10]}: {listing.stderr.strip()}")
    paths = [p for p in listing.stdout.split("\0") if p]
    if not paths:
        return {}
    batch = subprocess.run(
        ["git", "-C", str(root), "cat-file", "--batch"],
        input="".join(f"{sha}:{p}\n" for p in paths).encode("utf-8"),
        capture_output=True,
        timeout=120,
        env=scrubbed_git_env(),
    )
    if batch.returncode != 0:
        raise BaseUnavailable(f"cannot read {prefix} at the base {sha[:10]}: {batch.stderr.decode(errors='replace')}")
    texts, data, pos = {}, batch.stdout, 0
    for path in paths:
        header_end = data.index(b"\n", pos)
        header = data[pos:header_end].split()
        if len(header) < 3 or header[1] != b"blob":
            raise BaseUnavailable(f"unexpected object for {path} at the base: {data[pos:header_end]!r}")
        size = int(header[2])
        texts[path] = data[header_end + 1 : header_end + 1 + size].decode("utf-8", errors="replace")
        pos = header_end + 1 + size + 1
    return texts
