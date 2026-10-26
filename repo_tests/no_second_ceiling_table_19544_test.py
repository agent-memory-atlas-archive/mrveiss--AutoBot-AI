# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""A file-size ceiling is recorded in ONE table; a second hand-written one fails here (#19544).

The size gates kept every grandfathered ceiling twice (``KNOWN_LARGE`` and a mirrored
``RATCHET_BASELINE``), so every shrink edited two files and every pair of shrinks conflicted.
The mirror's job -- no ceiling raised, no entry added -- is now done against the base commit
(``repo_tests/_ratchet_base.py``). This guard stops the copy coming back under any name.

What it matches, by syntax tree and not by regex: an assignment (plain or annotated, at any
nesting) of a dict LITERAL in which at least two entries are ``"<path>.py|.ts|.vue|.sh": <int>``
with the int over the line limit the gates enforce -- a ceiling is by definition above it.
A derivation (``RATCHET_BASELINE = KNOWN_LARGE``) is not a literal, so it passes. A dict of
paths to a call-site count (1, 2, 3...), to a non-int, or with one such entry, is a different
concept: other ratchets' occurrence baselines are path->int tables too, and are not ceilings.
"""

from __future__ import annotations

import ast

from repo_tests._paths import repo_root

from tools.lint._scan_helpers import tracked_paths

#: The only modules allowed to hold a path->ceiling literal.
HOMES = frozenset(
    {
        "scripts/python_file_size_known_large.py",
        "scripts/frontend_file_size_known_large.py",
        "scripts/check_shell_file_size.py",
    }
)

#: Modules that hold such a table for a genuinely different concept: ``rel -> reason``.
EXEMPT: dict[str, str] = {
    "repo_tests/ratchet_base_19283_test.py": (
        "synthetic base/now dicts feeding raised_entries() -- fixtures of the base-commit check, not a table"
    ),
}

#: One over the 600-line limit every gate enforces (``test_the_threshold_is_the_gates_limit``).
_MIN_CEILING = 601

_SUFFIXES = (".py", ".ts", ".vue", ".sh")
_MIN_ENTRIES = 2
#: Cheap pre-filter so the AST is built only for files that can possibly match.
_NEEDLES = tuple(f'{suffix}"' for suffix in _SUFFIXES) + tuple(f"{suffix}'" for suffix in _SUFFIXES)


def _is_path_to_int(key: ast.expr | None, value: ast.expr) -> bool:
    return (
        isinstance(key, ast.Constant)
        and isinstance(key.value, str)
        and key.value.endswith(_SUFFIXES)
        and not any(glob in key.value for glob in "*?[")  # a glob is a pattern with a floor, not a path
        and isinstance(value, ast.Constant)
        and isinstance(value.value, int)
        and not isinstance(value.value, bool)
        and value.value >= _MIN_CEILING
    )


def ceiling_table_lines(source: str) -> list[int]:
    """Line of every assigned dict literal holding 2+ ``"<path>": <int>`` entries."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or not isinstance(node.value, ast.Dict):
            continue
        entries = sum(_is_path_to_int(k, v) for k, v in zip(node.value.keys, node.value.values, strict=True))
        if entries >= _MIN_ENTRIES:
            found.append(node.lineno)
    return found


def _offenders(root) -> dict[str, list[int]]:
    offenders = {}
    for rel in tracked_paths(root, "*.py"):
        if rel in HOMES or rel in EXEMPT:
            continue
        try:
            source = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not any(needle in source for needle in _NEEDLES):
            continue
        try:
            lines = ceiling_table_lines(source)
        except SyntaxError:
            continue
        if lines:
            offenders[rel] = lines
    return offenders


def test_no_second_hand_written_ceiling_table_exists() -> None:
    offenders = _offenders(repo_root())
    assert offenders == {}, (
        f"a path->int table outside the KNOWN_LARGE homes {sorted(HOMES)} (file: lines): {offenders}. "
        "Ceilings live in ONE table; derive from it (`RATCHET_BASELINE = KNOWN_LARGE`) or read it, "
        "never re-list it (#19544). A table of a genuinely different concept goes in EXEMPT with a reason."
    )


def test_every_exemption_gives_a_reason() -> None:
    assert all(reason.strip() for reason in EXEMPT.values()), "an exemption without a reason is a bypass"
    assert not set(EXEMPT) & HOMES


def test_the_homes_are_still_matched() -> None:
    """The control for the matcher: it MUST see the real tables, or an empty result means nothing."""
    root = repo_root()
    for rel in sorted(HOMES):
        assert ceiling_table_lines((root / rel).read_text(encoding="utf-8")), f"{rel} no longer matches the detector"


def test_the_threshold_is_the_gates_limit() -> None:
    """The threshold is derived from the gates, not a number that drifts from them."""
    import importlib.util

    for rel in ("scripts/check_python_file_size.py", "scripts/check_frontend_file_size.py"):
        spec = importlib.util.spec_from_file_location("_gate", repo_root() / rel)
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        assert gate.MAX_LINES + 1 == _MIN_CEILING, f"{rel} MAX_LINES moved; update _MIN_CEILING"


def test_a_planted_literal_is_matched() -> None:
    planted = 'RATCHET_BASELINE: dict[str, int] = {"a/b.py": 700, "a/c.ts": 800}\n'
    assert ceiling_table_lines(planted) == [1]
    assert ceiling_table_lines('T = {"x.vue": 601, "y.py": 602}') == [1]
    assert ceiling_table_lines("def f():\n    t = {'a.py': 601, 'b.py': 602}\n") == [2]


def test_a_derivation_and_other_dicts_are_not_matched() -> None:
    assert ceiling_table_lines("RATCHET_BASELINE = KNOWN_LARGE\n") == []
    assert ceiling_table_lines('T = {"a/b.py": 700}\n') == [], "one entry is not a table"
    assert ceiling_table_lines('T = {"a.py": "why", "b.py": "why"}\n') == [], "values are not ints"
    assert ceiling_table_lines('T = {"test_*.py": 790, "*_test.py": 1050}\n') == [], "globs are not paths"
    assert ceiling_table_lines('T = {"a.md": 1, "b.json": 2}\n') == [], "not source paths"
    assert ceiling_table_lines('T = {"a.py": 1, "b.py": 2}\n') == [], "call-site counts are not ceilings"
    assert ceiling_table_lines('T = {"a.py": True, "b.py": False}\n') == [], "bools are not ceilings"
    assert ceiling_table_lines('T = {**BASE, "a.py": 1}\n') == [], "a spread is not a literal table"
