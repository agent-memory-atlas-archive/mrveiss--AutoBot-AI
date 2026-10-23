# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The two non-selection checks tools/git-hooks/pre-push gained for #18808 stay wired.

Both caught defects only in CI before: the hardcoded-values baseline audit (#18708, #18790) and
the sys.modules leak guard (#18785). Their behaviour is tested where it lives
(pipeline-scripts/detect_hardcoded_values_audit_test.py; the probe module itself); this pins the
hook's invocations, whose loss would be silent -- a hook that skips a check still passes.
"""

from __future__ import annotations

from pathlib import Path

_HOOK = Path(__file__).resolve().parents[1] / "tools" / "git-hooks" / "pre-push"
_PROBE = "repo_tests/sys_modules_leak_probe_18808_test.py"


def _hook() -> str:
    return _HOOK.read_text(encoding="utf-8")


def test_the_hook_audits_the_baseline_scoped_to_the_changed_files():
    text = _hook()

    assert 'detect-hardcoded-values.sh" --audit-baseline --paths-from=' in text
    assert "hardcoded_values_baseline.txt" in text


def test_the_hook_collects_the_selection_serially_before_the_leak_probe():
    """Serial, because under xdist the probe can land on a worker that never imported the stub."""
    text = _hook()
    line = next(row for row in text.splitlines() if "--collect-only" in row and "leak_probe" in row)

    assert "-p no:xdist" in line
    assert line.index("$leak_targets") < line.index('"$leak_probe"'), "the probe must be collected last"
    assert f'leak_probe="{_PROBE}"' in text
    assert (_HOOK.parents[2] / _PROBE).is_file()
