# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Enrollment deploys the real agent through the slm_agent role (#18606).

Enrollment used to write a hand-written stand-in agent, embedded in a Python string, onto
the node. It now runs a tracked playbook that includes the `slm_agent` role, and the SLM
public key reaches it as an Ansible variable instead of by text substitution.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

_BACKEND = Path(__file__).resolve().parents[2]


def _load_real_deployment():
    """conftest stubs `services.deployment`; load the real file under its own name."""
    spec = importlib.util.spec_from_file_location("_real_deployment_18606", _BACKEND / "services" / "deployment.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deployment = _load_real_deployment()
DeploymentService = deployment.DeploymentService
_ANSIBLE = _BACKEND / "ansible"
_KEY = "ssh-rsa AAAAB3Nza slm-server@autobot"


def _tasks() -> list[dict]:
    play = yaml.safe_load((_ANSIBLE / deployment._ENROLLMENT_PLAYBOOK).read_text(encoding="utf-8"))[0]
    return play["tasks"]


def test_the_enrollment_playbook_is_a_tracked_file() -> None:
    assert (_ANSIBLE / deployment._ENROLLMENT_PLAYBOOK).is_file()
    assert deployment._ENROLLMENT_PLAYBOOK.startswith("playbooks/")


def test_the_playbook_includes_the_slm_agent_role_and_embeds_no_agent() -> None:
    tasks = _tasks()
    roles = [t["ansible.builtin.import_role"]["name"] for t in tasks if "ansible.builtin.import_role" in t]
    assert roles == ["autobot_shared", "slm_agent"]
    text = (_ANSIBLE / deployment._ENROLLMENT_PLAYBOOK).read_text(encoding="utf-8")
    assert "class SLMAgent" not in text
    assert all(t.get("when") is not False for t in tasks), "a disabled task is dead code"


def test_the_ssh_key_steps_are_kept() -> None:
    names = [t["name"] for t in _tasks()]
    for kept in (
        "Create autobot user",
        "Ensure .ssh directory exists for autobot user",
        "Deploy SLM server SSH public key for passwordless access",
        "Verify SSH key deployment succeeded",
        "Configure passwordless sudo for autobot user",
    ):
        assert kept in names


def _command(key: str) -> list[str]:
    svc = DeploymentService()
    return svc._build_enrollment_command(Path("p.yml"), "h", "n1", "autobot", 22, "https://slm", key)


def test_the_pubkey_travels_as_one_json_extra_var() -> None:
    cmd = _command(_KEY)
    arg = json.dumps({"slm_server_pubkey": _KEY})
    assert cmd.count(arg) == 1
    assert cmd[cmd.index(arg) - 1] == "-e"


def test_an_empty_key_is_passed_as_an_empty_var() -> None:
    assert json.dumps({"slm_server_pubkey": ""}) in _command("")


def test_the_template_and_its_helpers_are_gone() -> None:
    for name in ("_ENROLLMENT_PLAYBOOK_TEMPLATE",):
        assert not hasattr(deployment, name)
    for name in ("_create_enrollment_playbook", "_get_enrollment_playbook_content"):
        assert not hasattr(DeploymentService, name)


def _role_order(tasks: list[dict]) -> list[str]:
    return [t["ansible.builtin.import_role"]["name"] for t in tasks if "ansible.builtin.import_role" in t]


def test_autobot_shared_is_deployed_before_the_agent_that_imports_it() -> None:
    """The real agent imports autobot_shared; a fresh enrollee must get it in the same run."""
    roles = _role_order(_tasks())
    assert "autobot_shared" in roles and "slm_agent" in roles
    assert roles.index("autobot_shared") < roles.index("slm_agent")


def test_role_order_contrast() -> None:
    role = lambda name: {"ansible.builtin.import_role": {"name": name}}  # noqa: E731
    assert _role_order([role("autobot_shared"), role("slm_agent")]) == ["autobot_shared", "slm_agent"]
    assert "autobot_shared" not in _role_order([role("slm_agent")])
