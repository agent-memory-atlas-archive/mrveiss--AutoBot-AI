# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""scripts/repin_reach_floors.py rewrites exactly the stale floor it measured, and nothing else (#19282).

Re-pinning was a hand edit per floor, every train. The script measures each declaration and
rewrites its literal ``floor=`` mid-window; these tests pin the rewrite itself on fixture source:
the right call, the right line, the standard comment, an expression floor left for a human.
"""

from __future__ import annotations

import importlib.util

from repo_tests._paths import repo_root

_SPEC = importlib.util.spec_from_file_location("repin_reach_floors", repo_root() / "scripts" / "repin_reach_floors.py")
repin = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(repin)

_SOURCE = """\
OTHER = declare(
    "other-scan",
    discover=_d,
    floor=10,  # untouched
    growth=4,
)
REACH = declare(
    "the-scan",
    discover=_d,
    floor=100,  # mid-window: population 110, window (90, 110)
    growth=20,
)
"""


def test_the_named_declarations_literal_floor_is_rewritten_mid_window() -> None:
    new, status = repin.repin_source(_SOURCE, "the-scan", mid=140, count=150, low=130, high=150)
    assert status == "re-pinned"
    assert "    floor=140,  # mid-window: population 150, window (130, 150)\n" in new
    assert "    floor=10,  # untouched\n" in new, "another declaration's floor was touched"
    assert new.count("floor=") == 2


def test_an_expression_floor_is_left_for_a_human() -> None:
    source = 'R = declare(\n    "x",\n    discover=_d,\n    floor=_FLOOR,\n    growth=4,\n)\n'
    new, status = repin.repin_source(source, "x", mid=5, count=9, low=1, high=9)
    assert new == source and "not a literal integer" in status


def test_a_missing_declaration_is_reported_not_guessed() -> None:
    new, status = repin.repin_source(_SOURCE, "no-such-scan", mid=1, count=1, low=1, high=1)
    assert new == _SOURCE and status == "declare() call not found"
