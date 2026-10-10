# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""SLM agent identity and key-name hygiene (#18094)."""

from __future__ import annotations

import ast
import importlib.util

import pytest
from repo_tests._paths import repo_root

_ROOT = repo_root()
_STANDALONE = _ROOT / "autobot-infrastructure" / "shared" / "scripts" / "slm-agent-standalone.py"
_AGENT = _ROOT / "autobot-slm-backend" / "slm" / "agent" / "agent.py"
_MIRROR = _ROOT / "autobot-slm-backend" / "ansible" / "roles" / "slm_agent" / "files" / "slm" / "agent" / "agent.py"
_KEY_NAME = "AUTOBOT_INTERNAL_API_KEY"


def _load_standalone():
    spec = importlib.util.spec_from_file_location("slm_agent_standalone_18094", _STANDALONE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_standalone_agent_refuses_to_register_without_a_node_id(monkeypatch, tmp_path):
    monkeypatch.delenv("SLM_NODE_ID", raising=False)
    module = _load_standalone()

    with pytest.raises(SystemExit) as raised:
        module.SLMAgent(config_path=str(tmp_path / "absent.yaml"))

    assert raised.value.code == 1


def test_standalone_agent_uses_the_configured_node_id(monkeypatch, tmp_path):
    monkeypatch.setenv("SLM_NODE_ID", "node-under-test")
    module = _load_standalone()

    agent = module.SLMAgent(config_path=str(tmp_path / "absent.yaml"))

    assert agent.config["node_id"] == "node-under-test"


@pytest.mark.parametrize("path", [_AGENT, _MIRROR], ids=["canonical", "mirror"])
def test_agent_reads_the_key_through_the_shared_constant(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module == "autobot_shared"
        for a in n.names
    }
    literals = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == _KEY_NAME]
    attributes = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "INTERNAL_KEY_ENV"]

    assert "node_proxy" in imported
    assert attributes
    assert literals == []


def test_agent_and_mirror_stay_byte_identical():
    assert _AGENT.read_bytes() == _MIRROR.read_bytes()
