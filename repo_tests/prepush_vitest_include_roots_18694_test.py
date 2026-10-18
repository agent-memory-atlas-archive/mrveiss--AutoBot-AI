# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The pre-push hook hands vitest only files under a vitest include root (#18694).

A changed Playwright spec (`autobot-frontend/tests/visual/storybook-stories.spec.ts`)
matched the hook's `*.spec.ts` selector. vitest's config excludes `tests/**`, so
`vitest run <that file>` collected nothing, and the push was blocked as COULD NOT RUN.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from repo_tests._paths import repo_root

_HOOK = repo_root() / "tools" / "git-hooks" / "pre-push"
_CONFIGS = ("autobot-frontend", "autobot-slm-frontend", "libs/autobot-ui")
_ROOTS = re.compile(r"^VITEST_INCLUDE_ROOTS='(.+)'$", re.MULTILINE)
_SELECTION = re.compile(r"^    spec_files=\$\(.*?^    test_files=.*?$", re.MULTILINE | re.DOTALL)
_INCLUDE = re.compile(r"include:\s*\[(.*?)\]", re.DOTALL)


def _hook() -> str:
    return _HOOK.read_text(encoding="utf-8")


def _include_roots(project: str) -> set[str]:
    source = (repo_root() / project / "vitest.config.ts").read_text(encoding="utf-8")
    globs = re.findall(r"'([^']+)'", _INCLUDE.search(source).group(1))
    return {g.split("/", 1)[0] for g in globs}


def test_the_roots_are_the_union_of_every_vitest_include():
    declared = set(re.fullmatch(r"\^\((.+)\)/", _ROOTS.search(_hook()).group(1)).group(1).split("|"))
    assert declared == set().union(*(_include_roots(p) for p in _CONFIGS))


def _select(tmp_path, changed: list[str]) -> tuple[list[str], str]:
    for rel in changed:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("", encoding="utf-8")
    script = "\n".join(
        [
            _ROOTS.search(_hook()).group(0),
            'log() { echo "$*" >&2; }',
            'project=app; project_dir="$1"',
            "project_changed=$(printf 'app/%s\\n' " + " ".join(f"'{c}'" for c in changed) + ")",
            _SELECTION.search(_hook()).group(0),
            'echo "$test_files"',
        ]
    )
    run = subprocess.run(["bash", "-c", script, "_", str(tmp_path)], capture_output=True, text=True, check=True)
    return [line for line in run.stdout.splitlines() if line], run.stderr


@pytest.mark.parametrize(
    "changed, selected",
    [
        (["src/a.spec.ts", "tests/visual/storybook-stories.spec.ts"], ["src/a.spec.ts"]),
        (["scripts/b.test.ts", "e2e/flow.spec.ts"], ["scripts/b.test.ts"]),
        (["tests/visual/only.spec.ts"], []),
    ],
)
def test_only_include_root_files_reach_vitest(tmp_path, changed, selected):
    got, stderr = _select(tmp_path, changed)
    assert got == selected
    skipped = sorted(set(changed) - set(selected))
    assert all(s in stderr for s in skipped), "a skipped spec must be named, not dropped silently"
