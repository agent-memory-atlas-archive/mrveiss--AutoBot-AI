# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Tests for pre-commit-detect-mass-deletions (#4111, GH#14151 fail-closed
guard).

Issue #14151: see pre-commit-no-direct-redis_test.py's module docstring for
the shared background. This hook doesn't use get_staged_files() at all — it
calls `git diff --cached --diff-filter=D` directly into a bare top-level
assignment, so `set -e` alone (no lib/_common.sh change needed) is enough to
make a git failure abort instead of silently reading as "0 deletions,
allow". Its opening also references an unbound color variable only inside
the >50 violation branch, under `set -u`, which independently closed the
"missing lib/_common.sh" case pre-fix — only the git-failure path is the
genuine, newly-fixed defect here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from autobot_shared.isolated_git import isolated_git_env

HOOK_PATH = Path(__file__).resolve().parent / "pre-commit-detect-mass-deletions"


DELETION_THRESHOLD = 50


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True, env=isolated_git_env())


def _init_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _commit_and_stage_deletion_of(repo: Path, count: int) -> None:
    for i in range(count):
        (repo / f"f{i}.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed many files")
    for i in range(count):
        (repo / f"f{i}.txt").unlink()
    _git(repo, "add", "-A")


def test_allows_deletions_at_or_below_threshold(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit_and_stage_deletion_of(repo, DELETION_THRESHOLD)
    result = subprocess.run(["bash", str(HOOK_PATH)], cwd=repo, capture_output=True, text=True, env=isolated_git_env())
    assert result.returncode == 0, result.stdout + result.stderr


def test_blocks_mass_deletion_above_threshold(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _commit_and_stage_deletion_of(repo, DELETION_THRESHOLD + 10)
    result = subprocess.run(["bash", str(HOOK_PATH)], cwd=repo, capture_output=True, text=True, env=isolated_git_env())
    assert result.returncode != 0, result.stdout + result.stderr


class TestFailsClosedOnGitFailure:
    """GH#14151: a corrupted `.git/index` used to be indistinguishable from
    "0 files deleted" — reproduced with a genuine mass-deletion staged.
    """

    def test_a_git_failure_does_not_report_clean(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _commit_and_stage_deletion_of(repo, DELETION_THRESHOLD + 10)
        # Corrupt the index so git errors rather than returning an empty answer.
        (repo / ".git" / "index").write_text("garbage", encoding="utf-8")

        result = subprocess.run(
            ["bash", str(HOOK_PATH)], cwd=repo, capture_output=True, text=True, env=isolated_git_env()
        )
        assert result.returncode != 0, "a git failure was indistinguishable from '0 deletions'"


class TestMergeAware:
    """A merge that brings in the other side's deletions is not an accident (#18238).

    Catching an old branch up with main stages every file main removed since the
    branch was cut. Those deletions are absent from MERGE_HEAD, so they are not
    counted. A deletion of a file the merged-in side still has is counted as before.
    """

    def _branch_behind_a_main_that_deleted(self, tmp_path: Path, count: int) -> Path:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-q", "-b", "main")
        for i in range(count):
            (repo / f"f{i}.txt").write_text("x\n", encoding="utf-8")
        (repo / "keep.txt").write_text("k\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")
        _git(repo, "checkout", "-q", "-b", "old-branch")
        (repo / "branch-work.txt").write_text("b\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "branch work")
        _git(repo, "checkout", "-q", "main")
        for i in range(count):
            (repo / f"f{i}.txt").unlink()
        _git(repo, "add", "-A")
        _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "main removes files")
        _git(repo, "checkout", "-q", "old-branch")
        _git(repo, "merge", "--no-commit", "--no-ff", "main")
        return repo

    def _run(self, repo: Path) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(HOOK_PATH)], cwd=repo, capture_output=True, text=True, env=isolated_git_env())

    def test_a_catch_up_merge_of_the_other_sides_deletions_is_allowed(self, tmp_path: Path) -> None:
        repo = self._branch_behind_a_main_that_deleted(tmp_path, DELETION_THRESHOLD + 10)
        staged = _git(repo, "diff", "--cached", "--name-only", "--diff-filter=D").stdout.split()
        assert len(staged) == DELETION_THRESHOLD + 10  # the scenario really stages a mass deletion
        result = self._run(repo)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_contrast_deleting_files_the_merged_side_still_has_is_blocked(self, tmp_path: Path) -> None:
        repo = self._branch_behind_a_main_that_deleted(tmp_path, 5)
        # mid-merge, a stash-style accident drops many files that main still has
        for i in range(DELETION_THRESHOLD + 10):
            (repo / f"extra{i}.txt").write_text("e\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "finish merge")
        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "old-branch")
        _git(repo, "checkout", "-q", "old-branch")
        (repo / "late.txt").write_text("l\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "diverge")
        _git(repo, "merge", "--no-commit", "--no-ff", "main")
        for i in range(DELETION_THRESHOLD + 10):
            (repo / f"extra{i}.txt").unlink()
        _git(repo, "add", "-A")
        result = self._run(repo)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "extra0.txt" in result.stdout
