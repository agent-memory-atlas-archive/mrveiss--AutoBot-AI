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
from repo_tests._reach import declare

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

_RUNBOOK = repo_root() / "docs" / "operations" / "disaster-recovery.md"
_ROLES = "autobot-slm-backend/ansible/roles"
_UNIT_DEST = re.compile(r"/etc/systemd/system/([A-Za-z0-9@_.-]+)\.service")
_SYSTEMCTL = re.compile(r"systemctl\s+(?:status|start|restart|stop)\s+([^#\n]+)")
# Units installed by the packages the roles install, not written by a role template.
_PACKAGE_UNITS = {"nginx", "redis-stack-server", "postgresql"}


def _role_tasks(root) -> list[str]:
    """The role task files that can install a unit; ``[]`` on an empty tree, so the floor refuses it."""
    try:
        return tracked_paths(root, f"{_ROLES}/*/tasks/*.yml")
    except EmptyEnumeration:
        return []


REACH = declare(
    "disaster-recovery-deployed-units",
    discover=_role_tasks,
    floor=130,  # 138 measured at filing, pinned mid-window
    growth=16,
    what="role task files read for the systemd units they install (#19459)",
    roots=(_ROLES,),
)


def _deployed_units() -> set[str]:
    root = repo_root()
    units = set(_PACKAGE_UNITS)
    for rel in REACH.examined(root):
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
