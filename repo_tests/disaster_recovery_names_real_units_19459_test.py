# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Every unit the disaster-recovery runbook tells an operator to start is one the roles deploy (#19459).

The runbook named `npu-worker` and `playwright-server`, units no role installs, so a recovery
step failed with "Unit not found" in the middle of an outage. The roles deploy
`autobot-npu-worker` and `autobot-playwright`; the runbook now uses those, and this guard keeps
every `systemctl` step pointing at a unit a role writes or a package the roles install.
"""

from __future__ import annotations

import re

from repo_tests._paths import repo_root

from tools.lint._scan_helpers import tracked_paths

_RUNBOOK = repo_root() / "docs" / "operations" / "disaster-recovery.md"
_ROLES = "autobot-slm-backend/ansible/roles"
_UNIT_DEST = re.compile(r"/etc/systemd/system/([A-Za-z0-9@_.-]+)\.service")
_SYSTEMCTL = re.compile(r"systemctl\s+(?:status|start|restart|stop)\s+([^#\n]+)")
# Units installed by the packages the roles install, not written by a role template.
_PACKAGE_UNITS = {"nginx", "redis-stack-server", "postgresql"}


def _deployed_units() -> set[str]:
    root = repo_root()
    units = set(_PACKAGE_UNITS)
    for rel in tracked_paths(root, f"{_ROLES}/*/tasks/*.yml"):
        units.update(_UNIT_DEST.findall((root / rel).read_text(encoding="utf-8")))
    return units


def _runbook_units() -> set[str]:
    named = set()
    for args in _SYSTEMCTL.findall(_RUNBOOK.read_text(encoding="utf-8")):
        named.update(a for a in args.split() if not a.startswith("-"))
    return named


def test_the_runbook_names_units():
    """A control: the parser finds the units the runbook does name."""
    assert {"autobot-backend", "autobot-npu-worker", "autobot-playwright", "ollama"} <= _runbook_units()


def test_every_unit_the_runbook_names_is_deployed():
    missing = sorted(_runbook_units() - _deployed_units())
    assert not missing, f"disaster-recovery.md names units no role deploys: {missing}"
