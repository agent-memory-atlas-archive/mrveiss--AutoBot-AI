# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""install.sh sets its own umask before it creates anything (#19503).

It runs as root and clones the code tree that root later executes. With no umask of its own,
every mode was ``0777 & ~<caller's umask>``: world-writable on the clean-install CI runner, where
Ansible then refused the tree's ansible.cfg (role 'postgresql' not found). A world-writable tree
run as root lets any local account change what root runs.
"""

from __future__ import annotations

import re
import subprocess

from repo_tests._paths import repo_root


def _top_level_umask(source: str) -> str | None:
    """The umask install.sh sets at top level, before its first function definition."""
    head = source[: re.search(r"^\w+\(\)\s*\{", source, re.M).start()]
    match = re.search(r"^[^#\n]*\bumask (\d{3,4})\b", head, re.M)
    return match.group(1) if match else None


def test_the_installer_sets_umask_022_before_anything_runs() -> None:
    assert _top_level_umask((repo_root() / "install.sh").read_text(encoding="utf-8")) == "022"


def test_contrast_the_detector_needs_a_top_level_umask() -> None:
    assert _top_level_umask("set -e\nfoo() {\n  umask 022\n}\n") is None
    assert _top_level_umask("set -e\n# umask 022\nfoo() {\n}\n") is None
    assert _top_level_umask("set -e; umask 022  # why\nfoo() {\n}\n") == "022"


def test_the_umask_takes_effect_under_a_loose_caller(tmp_path) -> None:
    """The line as shipped, run under umask 000, makes a directory that is not world-writable."""
    line = next(
        line for line in (repo_root() / "install.sh").read_text(encoding="utf-8").splitlines() if "umask 022" in line
    )
    out = subprocess.run(
        ["bash", "-c", f"umask 000; {line}\nmkdir {tmp_path}/d && stat -c %a {tmp_path}/d"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert out.stdout.strip() == "755", out
