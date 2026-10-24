#!/usr/bin/env python3
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Lower the duplication-guard pins that a merge to main has left slack under (#16324).

`duplication_gate.py` fails a scope OVER its pin and only advises UNDER it, so a PR that
removes duplication without editing the pin leaves slack a later PR can spend. Editing the
pin in every dedup PR makes them all conflict on one line, so a PR under its pin stays
advisory and THIS runs after the merge: it reads the logs the guard job wrote on the push to
main and lowers each pin that is above its scope's measured count to that count.

It only ever lowers. A count over its pin, an UNMEASURED or non-numeric pin, and a log that
cannot be read are all left alone: raising a ratchet is a reviewed, declared scope change
(#18461), never an automatic one.

Usage:

    python3 scripts/duplication_pin_lowering.py WORKFLOW LOGDIR [--sha SHA]

Prints one ``VAR OLD -> NEW`` line per pin it lowered, rewrites WORKFLOW in place, and exits 0
whether or not anything changed (the caller tests ``git diff``). Exit 2: a log is missing or
unreadable, so no pin is touched.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import NamedTuple

_spec = importlib.util.spec_from_file_location("duplication_gate", Path(__file__).parent / "duplication_gate.py")
duplication_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(duplication_gate)  # type: ignore[union-attr]


class Scope(NamedTuple):
    """One jscpd scope: the env var holding its pin and the log its gate step tees."""

    pin_var: str
    log_name: str


#: Every scope duplication-guard.yml gates. repo_tests/duplication_guard_scopes_18559_test.py
#: holds this table to the workflow, so dropping a scope from either side fails.
SCOPES: tuple[Scope, ...] = (
    Scope("MAX_DUP_LINES", "main-dup.log"),
    Scope("SLM_MAX_DUP_LINES", "slm-gate.log"),
    Scope("TOOLING_MAX_DUP_LINES", "tooling-gate.log"),
    Scope("CROSS_GUI_MAX_DUP_LINES", "cross-gui-gate.log"),
    Scope("CROSS_BACKEND_MAX_DUP_LINES", "cross-backend-gate.log"),
)

_PIN_LINE = "^(?P<indent>[ ]+){var}: '(?P<value>[0-9]+)'[ ]*$"


def measure(logdir: Path) -> dict[str, int]:
    """Duplicated lines per pin variable; raises OSError/ValueError on an unreadable log."""
    counts: dict[str, int] = {}
    for scope in SCOPES:
        text = (logdir / scope.log_name).read_text(encoding="utf-8", errors="replace")
        counts[scope.pin_var] = duplication_gate.parse_totals(text).duplicated_lines
    return counts


def lower_pins(workflow: str, counts: dict[str, int], sha: str = "") -> tuple[str, list[tuple[str, int, int]]]:
    """Return the workflow text with every pin above its count lowered, and the changes."""
    changes: list[tuple[str, int, int]] = []
    where = f" on main {sha}" if sha else " on main"
    for var, measured in counts.items():
        pattern = re.compile(_PIN_LINE.format(var=re.escape(var)), re.MULTILINE)
        match = pattern.search(workflow)
        if match is None:
            continue
        old = int(match["value"])
        if measured >= old:
            continue
        indent = match["indent"]
        note = f"{indent}# {old} -> {measured} (#16324): lowered automatically to the count measured{where}.\n"
        replacement = f"{note}{indent}{var}: '{measured}'"
        workflow = workflow[: match.start()] + replacement + workflow[match.end() :]
        changes.append((var, old, measured))
    return workflow, changes


def main(argv: list[str]) -> int:
    sha = ""
    if "--sha" in argv:
        index = argv.index("--sha")
        sha, argv = argv[index + 1], argv[:index] + argv[index + 2 :]
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    workflow_path, logdir = Path(argv[0]), Path(argv[1])
    try:
        counts = measure(logdir)
    except (OSError, ValueError) as exc:
        print(f"could not read a scope's jscpd totals: {exc}. No pin was touched.", file=sys.stderr)
        return 2
    text, changes = lower_pins(workflow_path.read_text(encoding="utf-8"), counts, sha)
    if changes:
        workflow_path.write_text(text, encoding="utf-8")
    for var, old, new in changes:
        print(f"{var} {old} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
