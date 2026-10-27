# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""A per-play "no hosts matched" skip is not a failed fleet update (#19762).

``update-all-nodes.yml`` is multi-play; with ``limit=["localhost", node_id]`` every play for a
group the node is not in prints ``skipping: no hosts matched`` while the node's own plays run.
#19475 matched that line over the whole output and turned a clean run (``failed=0``) into a halted
stage. The inventory-level markers still fail the run, and the fleet outcome now requires the
NODE in the recap -- ``localhost`` is always there, so "some host ran" proves nothing about it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_NODE = "b9a29e04"


def _load_ansible_utils():
    """The shared parser, loaded standalone — it imports only stdlib + env_utils."""
    spec = importlib.util.spec_from_file_location("_au_19762", _BACKEND_ROOT / "services" / "ansible_utils.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_au_19762"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("_au_19762", None)
    return module


_au = _load_ansible_utils()


def _recap(node_ok: int = 100, node_failed: int = 0, with_node: bool = True) -> str:
    node = f"{_NODE}  : ok={node_ok}  changed=9  unreachable=0  failed={node_failed}  skipped=114\n"
    return (
        "PLAY RECAP ****\n" + (node if with_node else "") + "localhost  : ok=25  changed=4  unreachable=0  failed=0\n"
    )


# The shape of the 2026-10-27 run: the node's plays ran, three other-group plays skipped.
_SKIPS = "PLAY [Play 2b - Update Database Nodes] ****\nskipping: no hosts matched\n" * 3


def test_skips_from_other_groups_do_not_fail_a_run_the_node_completed() -> None:
    assert _au.ansible_run_failure(_SKIPS + _recap(), None, 0) is None
    assert _au.ansible_run_failure(_SKIPS + _recap(), _NODE, 0) is None
    assert _au.ansible_exit_code(_SKIPS + _recap(), None, 0) == 0


def test_the_inventory_level_markers_still_fail_the_whole_run() -> None:
    for marker in ("[WARNING]: No inventory was parsed\n", "[WARNING]: Could not match supplied host pattern, x\n"):
        assert _au.ansible_run_failure(marker + _recap(), None, 0)


def test_a_real_failure_in_the_recap_still_fails() -> None:
    assert _au.ansible_run_failure(_SKIPS + _recap(node_failed=1), _NODE, 0)


def test_a_run_that_skipped_the_node_entirely_fails_for_the_node_though_localhost_ran() -> None:
    absent = _SKIPS + _recap(with_node=False)
    assert _au.ansible_run_failure(absent, None, 0) is None  # localhost alone satisfies "some host ran"
    assert _au.ansible_run_failure(absent, _NODE, 0)  # which is why the fleet outcome names the node
    assert _au.ansible_run_failure(_SKIPS + _recap(node_ok=0), _NODE, 0)


def test_the_fleet_outcome_requires_the_node_itself_in_the_recap() -> None:
    """Read as source: importing ``api/code_sync.py`` pulls in the backend."""
    source = (_BACKEND_ROOT / "api" / "code_sync.py").read_text(encoding="utf-8")
    start = source.index("async def _record_fleet_node_outcome(")
    body = source[start : source.index("\nasync def ", start + 1)]
    success_line = next(line for line in body.splitlines() if 'if playbook_result["success"]' in line)
    assert 'ansible_run_failure(playbook_result["output"], node_id, 0) is None' in success_line
