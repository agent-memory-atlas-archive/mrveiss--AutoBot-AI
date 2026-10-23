# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""The down-only ratchets' base read: it catches a raise, and it refuses rather than guesses (#19283).

Hand-summed totals (MAX_CEILING_TOTAL, TOTAL_OCCURRENCES, css_rule_declarations,
MAX_KNOWN_LARGE_ENTRIES) were the tripwire for a raised entry, and every PR that lowered any
entry edited the same total line, so two such PRs always conflicted. The tripwire is now the base
commit's own table (repo_tests/_ratchet_base.py). These tests pin its two promises: a raised or
added entry is reported, and a base it cannot read is an error, never "nothing rose".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from repo_tests._ratchet_base import BaseUnavailable, base_commit, base_text, literal_in, raised_entries

from autobot_shared.isolated_git import git, make_repo


def test_a_raised_or_added_entry_is_reported_and_a_lowered_one_is_not() -> None:
    base = {"a.py": 700, "b.py": 650, "gone.py": 610}
    now = {"a.py": 701, "b.py": 640, "new.py": 900}
    assert raised_entries(now, base) == {"a.py": (700, 701), "new.py": (None, 900)}
    assert raised_entries({"a.py": 700}, base) == {}


def test_two_prs_lowering_different_entries_each_pass_against_the_same_base() -> None:
    """AC2: no shared line -- each PR's table is checked entry by entry against the base."""
    base = {"a.py": 700, "b.py": 650}
    assert raised_entries({"a.py": 690, "b.py": 650}, base) == {}
    assert raised_entries({"a.py": 700, "b.py": 640}, base) == {}
    assert raised_entries({"a.py": 690, "b.py": 640}, base) == {}


def _repo(tmp_path: Path, commits: int) -> Path:
    repo = make_repo(tmp_path / "repo")
    for n in range(commits):
        (repo / "table.py").write_text(f"KNOWN_LARGE = {{'x.py': {700 - n}}}\n", encoding="utf-8")
        git(repo, "add", "table.py")
        git(repo, "commit", "-q", "-m", f"c{n}")
    return repo


def test_in_ci_a_missing_head_parent_refuses_instead_of_passing(tmp_path, monkeypatch) -> None:
    """A depth-1 checkout has no HEAD^1: the ratchet must say so, never report 'nothing rose'."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(BaseUnavailable, match="fetch-depth: 2"):
        base_commit(_repo(tmp_path, commits=1))


def test_in_ci_the_base_is_head_parent_and_reads_its_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    repo = _repo(tmp_path, commits=2)
    assert literal_in(base_text(repo, "table.py"), "KNOWN_LARGE") == {"x.py": 700}
    assert base_text(repo, "not-there.py") is None


def test_locally_an_unresolvable_base_ref_refuses(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("AUTOBOT_RATCHET_BASE_REF", "origin/no-such-branch")
    with pytest.raises(BaseUnavailable, match="merge-base"):
        base_commit(_repo(tmp_path, commits=1))
