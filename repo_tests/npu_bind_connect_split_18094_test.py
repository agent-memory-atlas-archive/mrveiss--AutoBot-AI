# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""NPU_WORKER_HOST carried two meanings; the bind half has its own name (#18094)."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest
from repo_tests._paths import repo_root

_ROOT = repo_root()
_DOCKER = _ROOT / "autobot-infrastructure" / "autobot-npu-worker" / "docker"
_WORKER_FILES = ("npu_inference_server.py", "npu_worker_main.py")
_NEW_NAMES = (
    "NPU_WORKER_PORT",
    "NPU_WORKER_HOST",
    "NPU_WORKER_BIND_HOST",
    "SLM_NODE_ID",
    "SLM_SECRET_KEY",
    "SLM_ENCRYPTION_KEY",
    "SLM_USERS_DATABASE_URL",
    "OPENAI_API_KEY",
    "FLUX_API_KEY",
    "STABILITY_API_KEY",
)


def _host_expression(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "host":
            return compile(ast.Expression(node.value), str(path), "eval")
    raise AssertionError(f"no `host = ...` assignment in {path}")


def _bind_host(path: Path) -> str:
    return eval(_host_expression(path), {"os": os}, {})  # noqa: S307 - our own source; env patched by the caller


@pytest.mark.parametrize("name", _WORKER_FILES)
def test_worker_bind_host_precedence(name, monkeypatch):
    path = _DOCKER / name
    monkeypatch.delenv("NPU_WORKER_BIND_HOST", raising=False)
    monkeypatch.delenv("NPU_WORKER_HOST", raising=False)
    assert _bind_host(path) == "0.0.0.0"  # nosec B104

    monkeypatch.setenv("NPU_WORKER_HOST", "legacy-bind")
    assert _bind_host(path) == "legacy-bind"  # legacy deployments keep working

    monkeypatch.setenv("NPU_WORKER_BIND_HOST", "new-bind")
    assert _bind_host(path) == "new-bind"  # the new name wins


def test_ansible_sets_the_bind_address_under_the_new_name():
    text = (_ROOT / "autobot-slm-backend/ansible/inventory/group_vars/aiml.yml").read_text(encoding="utf-8")
    assert 'NPU_WORKER_BIND_HOST: "0.0.0.0"' in text
    assert "    NPU_WORKER_HOST:" not in text


def test_live_names_are_registered_unrenamed():
    from autobot_shared.env_registry import REGISTRY

    missing = [n for n in _NEW_NAMES if n not in REGISTRY]
    assert missing == []
    assert all(REGISTRY[n].component in {"npu", "slm", "image_generation"} for n in _NEW_NAMES)
