# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The pre-push scan-root record matches every live ``roots=`` declaration (#18808).

A parse of ``declare(`` calls resolved 31 of 90 ``roots=`` sites -- constants, imports and
comprehensions were silently unselected (#19312 review). The record is now computed from the
registry (repo_tests/_reach_roots.py); this pins it, both ways.
"""

from __future__ import annotations

from repo_tests._reach_roots import expected_record, render
from repo_tests._reach_roots_record import REACH_ROOTS


def test_the_record_matches_every_live_roots_declaration():
    expected = expected_record()
    assert REACH_ROOTS == expected, (
        "repo_tests/_reach_roots_record.py is out of date. Regenerate it:\n"
        "    python -m repo_tests._reach_roots\n"
        f"missing or changed: {sorted(k for k in expected if REACH_ROOTS.get(k) != expected[k])}; "
        f"stale: {sorted(set(REACH_ROOTS) - set(expected))}"
    )


def test_a_roots_constant_resolves_where_the_call_parse_did_not():
    """Control: `ai_line_single_policy_18430` writes `roots=_ROOTS`, the shape the parse missed."""
    assert "repo_tests/ai_line_single_policy_18430_test.py" in expected_record()


def test_the_record_renders_to_its_own_source():
    from pathlib import Path  # noqa: PLC0415

    path = Path(__file__).with_name("_reach_roots_record.py")
    assert path.read_text(encoding="utf-8") == render(REACH_ROOTS)
