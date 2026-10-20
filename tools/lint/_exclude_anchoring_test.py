# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Tests for tools/lint/_exclude_anchoring.py — the decisions both exclude-anchoring guards share (#18954).

Driven with synthetic entry lists and the two tools' differing parameters, so the
shared functions are pinned independently of the real ``.flake8`` / ``.bandit``.
"""

from __future__ import annotations

import functools
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_PATH = Path(__file__).parent / "_exclude_anchoring.py"
_spec = importlib.util.spec_from_file_location("_exclude_anchoring_under_test", _PATH)
assert _spec is not None and _spec.loader is not None
shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shared)

_flake8_bare = functools.partial(shared.bare_entries, separators="/\\", skip_globs=False)
_bandit_bare = functools.partial(shared.bare_entries, separators="/", skip_globs=True)


def test_bare_entries_keeps_separator_free_names_and_drops_anchored_ones():
    entries = ["tests", "a/b/", "node_modules", "x\\y"]
    assert _flake8_bare(entries) == ["tests", "node_modules"]  # backslash is a separator for flake8


def test_bare_entries_backslash_is_not_a_separator_for_bandit():
    assert _bandit_bare(["x\\y", "/tests/"]) == ["x\\y"]


def test_bare_entries_skips_globs_only_when_asked():
    assert _bandit_bare(["*.egg-info", "venv"]) == ["venv"]
    assert _flake8_bare(["*.egg-info", "venv"]) == ["*.egg-info", "venv"]


def test_unanchored_source_entries_flags_a_bare_name_that_is_not_an_artifact():
    flagged = shared.unanchored_source_entries(["tests", "venv", "a/b/"], _flake8_bare, {"venv"})
    assert flagged == ["tests"]


def test_unanchored_source_entries_passes_artifact_names_and_anchored_entries():
    assert shared.unanchored_source_entries(["venv", "a/b/"], _flake8_bare, {"venv"}) == []


def _component_hits(entry: str, path: str) -> int:
    return path.split("/")[:-1].count(entry)


def _substring_hits(entry: str, path: str) -> int:
    return int(entry in path)


def test_entries_covering_tracked_python_counts_hits_per_bare_entry_in_config_order():
    tracked = ["monitoring/a.py", "x/monitoring/b.py", "src/c.py"]
    covered = shared.entries_covering_tracked_python(
        ["src", "monitoring", "monitoring"], tracked, _flake8_bare, _component_hits
    )
    assert covered == {"src": 1, "monitoring": 2}
    assert list(covered) == ["src", "monitoring"]  # duplicates collapsed, order kept


def test_entries_covering_tracked_python_ignores_anchored_entries_and_zero_hits():
    tracked = ["a/b/c.py"]
    assert shared.entries_covering_tracked_python(["a/b/", "venv"], tracked, _flake8_bare, _component_hits) == {}


def test_entries_covering_tracked_python_uses_the_callers_matcher():
    """bandit's substring matcher sees ``venv`` in a filename; flake8's component matcher does not."""
    tracked = ["tools/check_venv_producers.py"]
    assert shared.entries_covering_tracked_python(["venv"], tracked, _bandit_bare, _substring_hits) == {"venv": 1}
    assert shared.entries_covering_tracked_python(["venv"], tracked, _flake8_bare, _component_hits) == {}
