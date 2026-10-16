# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The pre-push pytest budget follows the selected work (#17451).

A hub file changes one path and selects dozens of guards; sizing by changed-file
count gave it the SMALLER budget and it timed out. Drives the hook's real
``pytest_budget`` function (extracted from the script, not reimplemented).
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest
from repo_tests._paths import repo_root

HOOK = repo_root() / "tools" / "git-hooks" / "pre-push"
_BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(_BASH is None, reason="bash not found -- did not look, not a pass")


def _budget(changed: int, selected: int, parallel: int) -> int:
    src = HOOK.read_text(encoding="utf-8")
    fn = re.search(r"^pytest_budget\(\) \{.*?^\}", src, re.S | re.M)
    assert fn, "pytest_budget() missing from the pre-push hook"
    script = f"{fn.group(0)}\npytest_budget $((120 + {changed} * 4)) {selected} {parallel}\n"
    out = subprocess.run([_BASH, "-c", script], capture_output=True, text=True, check=True)
    return int(out.stdout)


@pytest.mark.parametrize("parallel", [0, 1])
def test_hub_file_gets_no_smaller_budget_than_wide_changeset(parallel):
    hub = _budget(changed=4, selected=28, parallel=parallel)
    wide = _budget(changed=22, selected=0, parallel=parallel)
    assert hub >= wide


def test_budget_grows_with_selection_and_is_capped():
    assert _budget(5, 46, 0) > _budget(5, 6, 0)
    assert _budget(100, 500, 0) == 600


def test_parallel_budget_is_not_larger_than_serial():
    assert _budget(15, 46, 1) <= _budget(15, 46, 0)


def test_hook_gates_parallel_on_xdist_import_and_enforces_printed_budget():
    src = HOOK.read_text(encoding="utf-8")
    assert "import xdist" in src
    assert 'timeout "$PYTEST_TIMEOUT"' in src and "budget ${PYTEST_TIMEOUT}s" in src
