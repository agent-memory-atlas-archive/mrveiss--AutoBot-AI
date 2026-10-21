#!/usr/bin/env python3
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A vitest that fails only on a dependency the push ADDS is COULD NOT RUN (#19130).

Runs the hook's **own** functions (extracted verbatim) against a real throwaway git
repo, so the package.json/lockfile diff and the node_modules check are exercised end
to end. A reimplementation here would agree with itself and prove nothing.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from repo_tests._paths import repo_root

from autobot_shared.paths import scrubbed_git_env

_HOOK_DIR = repo_root() / "tools" / "git-hooks"
_SOURCE = (_HOOK_DIR / "pre-push").read_text(encoding="utf-8")
_UNRESOLVED = 'Error: Failed to resolve import "dompurify" from "src/a.ts". Does the file exist?\n FAIL  a.test.ts\n'
_PASS_PLUS_BROKEN = 'Error: Failed to resolve import "vue" from "src/a.ts". Does the file exist?\n FAIL  a.test.ts\n'


def _fn(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(?:.*\n)*?\}}$", _SOURCE, re.MULTILINE)
    assert match, f"the hook no longer defines {name} — move this test with it, do not delete it"
    return match.group(0)


def _bash(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True, text=True, check=False)


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=scrubbed_git_env(),
    )
    return done.stdout.strip()


def _repo(tmp_path: Path, *, lock: bool = False) -> tuple[Path, str, str]:
    """A repo whose second commit adds `dompurify` to app/package.json (and its lockfile)."""
    (tmp_path / "app").mkdir()
    _git(tmp_path, "init", "-q")
    manifest, lockfile = tmp_path / "app" / "package.json", tmp_path / "app" / "package-lock.json"
    manifest.write_text(json.dumps({"dependencies": {"vue": "1"}}), encoding="utf-8")
    lockfile.write_text(json.dumps({"packages": {"": {}, "node_modules/vue": {}}}), encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    if lock:
        lockfile.write_text(
            json.dumps({"packages": {"node_modules/vue": {}, "node_modules/@s/p/node_modules/dompurify": {}}})
        )
    else:
        manifest.write_text(json.dumps({"dependencies": {"vue": "1", "dompurify": "3"}}), encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "add dep")
    return tmp_path, base, _git(tmp_path, "rev-parse", "HEAD")


def _install(root: Path, *names: str) -> Path:
    project = root / "app"
    for name in names:
        (project / "node_modules" / name).mkdir(parents=True)
        (project / "node_modules" / name / "package.json").write_text("{}", encoding="utf-8")
    return project


def _verdict(root: Path, base: str, head: str, output: str, project: Path) -> subprocess.CompletedProcess[str]:
    script = "\n".join(
        [
            f"REPO_ROOT={root}; PYTEST_PYTHON={sys.executable}",
            _fn("absent_added_deps"),
            _fn("unresolved_packages"),
            _fn("vitest_failed_on_new_dep_only"),
            f'absent=$(absent_added_deps {base} {head} {project}); echo "absent=[$absent]"',
            'vitest_failed_on_new_dep_only "$1" "$absent"',
        ]
    )
    return subprocess.run(
        ["bash", "-c", script, "x", output],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=scrubbed_git_env(),
    )


def _stage_tool(root: Path) -> None:
    target = root / "tools" / "git-hooks"
    target.mkdir(parents=True)
    (root / "autobot_shared").symlink_to(repo_root() / "autobot_shared")
    (target / "added_js_deps.py").write_text(
        (_HOOK_DIR / "added_js_deps.py").read_text(encoding="utf-8"), encoding="utf-8"
    )


class TestAnAddedAbsentDependencyIsCouldNotRun:
    def test_added_and_absent_defers_to_ci(self, tmp_path: Path) -> None:
        root, base, head = _repo(tmp_path)
        _stage_tool(root)
        done = _verdict(root, base, head, _UNRESOLVED, _install(root, "vue"))
        assert "absent=[dompurify]" in done.stdout
        assert done.returncode == 0, done.stderr

    def test_a_lockfile_only_addition_is_detected(self, tmp_path: Path) -> None:
        root, base, head = _repo(tmp_path, lock=True)
        _stage_tool(root)
        done = _verdict(root, base, head, _UNRESOLVED, _install(root, "vue"))
        assert "absent=[dompurify]" in done.stdout and done.returncode == 0, done.stderr

    def test_dependency_present_in_the_install_runs_vitest(self, tmp_path: Path) -> None:
        root, base, head = _repo(tmp_path)
        _stage_tool(root)
        done = _verdict(root, base, head, _UNRESOLVED, _install(root, "vue", "dompurify"))
        assert "absent=[]" in done.stdout
        assert done.returncode != 0, "a dependency that IS installed must not defer — vitest's failure stands"

    def test_a_broken_import_of_an_existing_module_still_fails(self, tmp_path: Path) -> None:
        root, base, head = _repo(tmp_path)
        _stage_tool(root)
        done = _verdict(root, base, head, _PASS_PLUS_BROKEN, _install(root, "vue"))
        assert "absent=[dompurify]" in done.stdout
        assert done.returncode != 0, "an unresolved EXISTING module must never be waved through"

    def test_a_broken_existing_import_beside_the_new_dep_still_fails(self, tmp_path: Path) -> None:
        root, base, head = _repo(tmp_path)
        _stage_tool(root)
        done = _verdict(root, base, head, _UNRESOLVED + _PASS_PLUS_BROKEN, _install(root, "vue"))
        assert done.returncode != 0

    def test_a_failure_naming_no_unresolved_import_still_fails(self, tmp_path: Path) -> None:
        root, base, head = _repo(tmp_path)
        _stage_tool(root)
        done = _verdict(
            root, base, head, " FAIL  a.test.ts\nAssertionError: expected 2 to be 3\n", _install(root, "vue")
        )
        assert done.returncode != 0


class TestTheDeferralUsesTheExistingCouldNotRunPath:
    def test_both_vitest_branches_call_could_not_run_before_failing(self) -> None:
        for fail_marker in ('fail "vitest failures (', 'fail "vitest failures after'):
            deferral = _SOURCE.find("vitest_failed_on_new_dep_only")
            assert 0 < deferral < _SOURCE.find(fail_marker, deferral)
        assert _SOURCE.count("new dependency; CI's npm ci verifies") == 2

    def test_allow_unrunnable_semantics(self, tmp_path: Path) -> None:
        stubs = 'RED=; CLR=; warn() { echo "WARN $*" >&2; }; EXIT_CODE=0\n'
        for allow, code in (("0", "1"), ("1", "0")):
            script = (
                f'{stubs}ALLOW_UNRUNNABLE={allow}\n{_fn("could_not_run")}\ncould_not_run vitest "r"; echo $EXIT_CODE'
            )
            assert _bash(script, tmp_path).stdout.strip() == code
