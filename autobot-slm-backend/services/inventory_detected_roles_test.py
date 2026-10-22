# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Observation must not promote a node into slm_server (#14513).

Live failure: a node whose declared roles were ``[vnc, slm-agent]`` was targeted
by ``update-all-nodes.yml``'s "Play 1 - Update SLM Server First". Play 1 deployed
the manager's backend, frontend and shared tree onto it and then died on::

    git -C /opt/autobot/code_source rev-parse HEAD   ->  rc 128
    fatal: cannot change to '/opt/autobot/code_source': No such file or directory

The node reached ``slm_server`` through ``detected_roles``, which recorded every
role the agent PROBED rather than the ones it found. Two nodes running entirely
different things reported byte-identical 20-entry lists.

Two independent defences are asserted here, because either alone is
insufficient:

1. the report filter, so the false data stops being recorded; and
2. the inventory guard, because a node this bug already contaminated will
   legitimately detect ``slm-backend`` afterwards -- the filter cannot help it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

_SLM_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relative: str):
    """Load a module from disk, past the package conftest's `services.*` stubs.

    No `sys.path` mutation: `inventory_builder` imports nothing from `services.*`
    or `api.*`, only `autobot_shared`, which pytest.ini already puts on the path.
    pytest.ini keeps `autobot-backend/` and `autobot-slm-backend/` in separate
    invocations precisely because they define identically-named top-level
    packages, and a stray path insert is how that separation gets undone.

    The module is registered under a private name and removed again, mirroring
    `inventory_builder_agent_group_test.py`.
    """
    spec = importlib.util.spec_from_file_location(name, _SLM_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


inventory_builder = _load("inventory_builder_under_test", "services/inventory_builder.py")


def _node(declared, detected):
    return SimpleNamespace(roles=list(declared), detected_roles=list(detected))


def _groups(node):
    """Groups this node ends up in (declared roles only, #19197) — what the inventory writes."""
    return inventory_builder.groups_for_role_tokens(inventory_builder._declared_roles(node))


# The full catalogue every agent probes, as observed live on both nodes.
_PROBED = [
    "ai-stack",
    "autobot-llm-cpu",
    "autobot-llm-gpu",
    "autobot_shared",
    "backend",
    "browser-service",
    "celery",
    "chromadb",
    "frontend",
    "npu-worker",
    "postgres",
    "redis",
    "scheduler",
    "slm-agent",
    "slm-backend",
    "slm-database",
    "slm-frontend",
    "slm-monitoring",
    "tts-worker",
    "vnc",
]


def test_the_module_really_loaded():
    """A stubbed module would satisfy every attribute lookup below."""
    assert callable(inventory_builder.groups_for_role_tokens)
    assert callable(inventory_builder._declared_roles)
    assert "slm_server" in inventory_builder._DECLARED_ONLY_GROUPS


def test_detection_adds_no_groups():
    """#19197: Ansible manages only the services ASSIGNED to a node.

    Reverses the pre-#19197 contract that a node running an undeclared redis
    keeps receiving redis plays; detection is informational.
    """
    groups = _groups(_node(["backend"], ["redis"]))

    assert "backend" in groups
    assert "redis" not in groups and "database" not in groups


def test_a_node_that_declares_nothing_gets_no_role_groups_from_detection():
    """No declared roles: only the universal groups, whatever is detected."""
    groups = _groups(_node([], ["redis"]))

    assert "redis" not in groups and "database" not in groups


def test_a_node_that_declares_nothing_is_not_a_deploy_target():
    """No declared roles means no operator intent to deploy anything to it.

    Detection alone must not make a node receive the backend tree and the
    alembic sequence — that is the contamination this PR exists to stop, and a
    node with nothing declared is the weakest possible evidence of intent.
    """
    node = _node([], ["backend", "slm-backend"])
    groups = _groups(node)

    assert "backend" not in groups and "main" not in groups
    assert "slm_server" not in groups


def test_a_vnc_node_does_not_join_slm_server():
    """The exact live failure.

    `slm_server` is what "Play 1 - Update SLM Server First" targets.
    """
    node = _node(["vnc", "slm-agent"], _PROBED)
    groups = _groups(node)

    assert "slm_server" not in groups, (
        "a vnc/slm-agent node is still in slm_server — Play 1 will deploy the manager tree "
        "onto it and fail on the missing code_source checkout (#14513)"
    )


def test_an_already_contaminated_node_still_does_not_join_slm_server():
    """The case the report filter cannot fix.

    This bug unpacked the SLM tree onto fleet nodes, so their agents will now
    report slm-backend as genuinely installed — a true detection of a state the
    bug itself created. Declared roles are what must decide.
    """
    node = _node(["vnc", "slm-agent"], ["slm-backend", "slm-frontend", "vnc", "slm-agent"])
    groups = _groups(node)

    assert (
        "slm_server" not in groups
    ), "a contaminated node re-qualifies for slm_server, so the failure repeats every update"


def test_the_real_manager_keeps_slm_server():
    """The guard must not lock the actual manager out of its own play."""
    node = _node(["slm-backend", "slm-frontend", "slm-database", "slm-monitoring"], _PROBED)
    groups = _groups(node)

    assert "slm_server" in groups, "the SLM manager lost slm_server — Play 1 would never run"


def test_a_contaminated_node_is_also_kept_out_of_the_backend_group():
    """Review of #14513: `slm_server` alone was under-inclusive.

    Play 2 unarchives autobot-backend onto any host with `'backend' in
    group_names` and runs the alembic upgrade sequence there. It also sets
    `any_errors_fatal: true` with `serial: 3`, so a single wrongly-included node
    aborts the whole batch of legitimate hosts -- a wider blast radius than the
    Play 1 failure that surfaced this bug.

    The same contamination that put the SLM tree on a VNC node would have put
    the backend tree there too, and detection would then keep re-granting it.
    """
    node = _node(["vnc", "slm-agent"], ["slm-backend", "backend", "vnc", "slm-agent"])
    groups = _groups(node)

    assert "backend" not in groups, "a node that never declared backend is still a backend deploy target"
    assert "main" not in groups, "`main` is granted alongside `backend` and must be gated with it"


def test_a_declared_backend_node_keeps_its_groups():
    """The gate must not lock a real backend node out of its own deploy."""
    groups = _groups(_node(["backend"], ["backend"]))

    assert "backend" in groups and "main" in groups


def test_vnc_node_detecting_the_whole_stack_is_in_no_service_group():
    """#19197 contrast: declared [vnc, slm-agent], detecting service roles."""
    node = _node(["vnc", "slm-agent"], ["postgres", "chromadb", "slm-database", "slm-monitoring", "redis"])
    groups = _groups(node)

    for leaked in ("redis", "database", "ai_stack", "monitoring_vm", "slm_server"):
        assert leaked not in groups, f"{leaked} granted by detection alone"
    assert "slm_nodes" in groups


def test_the_slm_node_declaring_everything_is_unchanged():
    node = _node(["slm-backend", "slm-frontend", "slm-database", "slm-monitoring", "slm-agent"], _PROBED)
    groups = _groups(node)

    for expected in ("slm_server", "redis", "monitoring_vm"):
        assert expected in groups, f"declared SLM node lost {expected}"


def test_a_declared_role_maps_to_its_groups():
    groups = _groups(_node(["redis"], []))

    assert "redis" in groups and "database" in groups


def test_undeclared_detections_are_surfaced_not_managed():
    node = _node(["vnc", "slm-agent"], ["redis", "vnc", "postgres"])

    assert inventory_builder._undeclared_detected_roles(node) == ["postgres", "redis"]
