#!/usr/bin/env python3
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Re-pin every stale reach floor mid-window in one command (#19282).

A reach floor goes stale when the tree outgrows it (repo_tests/_reach.py ``Reach.staleness``).
Merge trains enforce staleness (``AUTOBOT_REACH_AUDIT=1``); this script is how a train fixes it:
it imports every guard, measures each floor-based declaration against the live tree, and rewrites
each stale ``floor=<int>`` inside its ``declare(...)`` call to the window's mid value with the
standard comment. ``--check`` changes nothing and exits 1 if any floor is stale.

Only literal integer floors are rewritten. A floor written as an expression is reported, not
touched, so a human decides what it should derive from.

Usage:
    python scripts/repin_reach_floors.py           # re-pin every stale floor
    python scripts/repin_reach_floors.py --check   # report, exit 1 if any is stale
"""

from __future__ import annotations

import argparse
import inspect
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _declarations() -> list:
    """Every live floor-based declaration, after importing every guard (the meta-test's walk)."""
    from repo_tests import reach_declarations_test as meta  # noqa: PLC0415 - imports every guard

    failures = getattr(meta, "IMPORT_FAILURES", {})
    if failures:
        # Named, never silent: those floors are not measured here; the train's CI audit
        # (AUTOBOT_REACH_AUDIT=1) still checks them where every guard imports.
        names = ", ".join(sorted(failures))
        print(
            f"WARNING: {len(failures)} guard module(s) failed to import here, "
            f"so their floors are NOT measured: {names}",
            file=sys.stderr,
        )
    return [r for r in meta._declarations() if r.min_fraction is None]


def _declare_call(source: str, name: str) -> tuple[int, int] | None:
    """(start, end) of the ``declare(`` call whose first argument is *name*, or None."""
    m = re.search(rf"declare\(\s*\"{re.escape(name)}\"", source)
    if not m:
        return None
    depth, i = 0, m.start() + len("declare")
    for j in range(i, len(source)):
        depth += source[j] == "("
        depth -= source[j] == ")"
        if depth == 0:
            return m.start(), j + 1
    return None


def repin_source(source: str, name: str, mid: int, count: int, low: int, high: int) -> tuple[str, str]:
    """*source* with *name*'s literal floor set to *mid*; returns (new source, status)."""
    span = _declare_call(source, name)
    if span is None:
        return source, "declare() call not found"
    call = source[span[0] : span[1]]
    floor = re.search(r"^(\s*floor=)(\d+)(,)[^\n]*$", call, re.M)
    if floor is None:
        return source, "floor is not a literal integer -- re-pin by hand"
    line = f"{floor.group(1)}{mid}{floor.group(3)}  # mid-window: population {count}, window ({low}, {high})"
    new_call = call[: floor.start()] + line + call[floor.end() :]
    return source[: span[0]] + new_call + source[span[1] :], "re-pinned"


def _source_file(reach) -> Path | None:
    """The file whose source holds this declaration's ``declare(`` call."""
    module = sys.modules.get(getattr(reach.discover, "__module__", ""))
    candidates = [Path(inspect.getsourcefile(module))] if module else []
    candidates += sorted(_ROOT.glob("repo_tests/*.py"))
    for path in candidates:
        if _declare_call(path.read_text(encoding="utf-8"), reach.name):
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="report stale floors; change nothing")
    args = parser.parse_args()
    stale = [(r, r.staleness(_ROOT)) for r in _declarations()]
    stale = [(r, why) for r, why in stale if why]
    if not stale:
        print("No stale reach floors.")
        return 0
    if args.check:
        for reach, why in stale:
            print(f"STALE {reach.name}: {why.splitlines()[0]}")
        print(f"{len(stale)} stale floor(s). Fix with: python scripts/repin_reach_floors.py")
        return 1
    unfixed = 0
    for reach, _why in stale:
        low, high, mid = reach.window(_ROOT)
        count = len(reach.population(_ROOT))
        path = _source_file(reach)
        if path is None:
            print(f"SKIP  {reach.name}: declare() call not found")
            unfixed += 1
            continue
        new, status = repin_source(path.read_text(encoding="utf-8"), reach.name, mid, count, low, high)
        if status == "re-pinned":
            path.write_text(new, encoding="utf-8")
            print(f"PIN   {reach.name}: floor -> {mid} (population {count}) in {path.relative_to(_ROOT)}")
        else:
            print(f"SKIP  {reach.name}: {status} ({path.relative_to(_ROOT)})")
            unfixed += 1
    return 1 if unfixed else 0


if __name__ == "__main__":
    sys.exit(main())
