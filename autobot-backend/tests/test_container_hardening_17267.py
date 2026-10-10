# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Container hardening kwargs each Docker client passes to docker, before == after (#17267).

The expected dicts are the literals the two clients hard-coded before the builder
existed; the mock docker client captures what actually reaches ``create``/``run``.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

import services.docker_task_workspace as dtw
from autobot_shared.container_hardening import hardening_kwargs
from secure_sandbox_executor import SandboxConfig, SandboxSecurityLevel, SecureSandboxExecutor

_HARDENING_KEYS = (
    "network_mode",
    "read_only",
    "security_opt",
    "cap_drop",
    "cap_add",
    "mem_limit",
    "memswap_limit",
    "cpu_quota",
    "cpu_period",
    "pids_limit",
)


def _hardening(kwargs: dict) -> dict:
    return {k: kwargs[k] for k in _HARDENING_KEYS if k in kwargs}


def _sandbox_create_kwargs(config: SandboxConfig) -> dict:
    executor = object.__new__(SecureSandboxExecutor)
    executor.docker_client = MagicMock()
    executor.container_prefix = "autobot-sandbox-"
    executor.docker_client.containers.create(**executor._prepare_container_config(["echo", "hi"], config))
    return executor.docker_client.containers.create.call_args.kwargs


@pytest.mark.parametrize(
    ("level", "network", "cap_add"),
    [
        (SandboxSecurityLevel.HIGH, False, []),
        (SandboxSecurityLevel.MEDIUM, False, ["DAC_OVERRIDE", "CHOWN"]),
        (SandboxSecurityLevel.LOW, True, ["DAC_OVERRIDE", "CHOWN", "FOWNER", "SETUID", "SETGID"]),
    ],
)
def test_secure_sandbox_create_kwargs_are_unchanged(level, network, cap_add) -> None:
    cfg = SandboxConfig(security_level=level, enable_network=network, memory_limit="256m", cpu_limit=0.5)
    assert _hardening(_sandbox_create_kwargs(cfg)) == {
        "network_mode": "bridge" if network else "none",
        "read_only": True,
        "security_opt": ["no-new-privileges"],
        "cap_drop": ["ALL"],
        "cap_add": cap_add,
        "mem_limit": "256m",
        "memswap_limit": "256m",
        "cpu_quota": 50000,
        "cpu_period": 100000,
    }


@pytest.mark.parametrize("storage_quota", [False, True])
def test_workspace_run_kwargs_are_unchanged(monkeypatch, storage_quota) -> None:
    monkeypatch.setattr(dtw, "_DISK_QUOTA_ENABLED", storage_quota)
    manager = object.__new__(dtw.TaskWorkspaceManager)
    manager._docker = MagicMock()
    monkeypatch.setattr(manager, "_reap_container_by_name", lambda name: None, raising=False)
    monkeypatch.setattr(time, "time", lambda: 1.0)
    manager._start_container_sync("task-abc", "vol-abc", "img:1")
    kwargs = manager._docker.containers.run.call_args.kwargs
    assert _hardening(kwargs) == {
        "network_mode": "none",
        "read_only": False,
        "security_opt": ["no-new-privileges"],
        "cap_drop": ["ALL"],
        "cap_add": [],
        "mem_limit": dtw._WORKSPACE_MEMORY_LIMIT,
        "memswap_limit": dtw._WORKSPACE_MEMORY_LIMIT,
        "cpu_quota": dtw._WORKSPACE_CPU_QUOTA,
        "cpu_period": 100000,
        "pids_limit": dtw._WORKSPACE_PIDS_LIMIT,
    }


def test_builder_pids_limit_is_omitted_unless_given() -> None:
    base = {"network_mode": "none", "read_only": True, "memory_limit": "1m", "cpu_quota": 1}
    assert "pids_limit" not in hardening_kwargs(**base)
    assert hardening_kwargs(**base, pids_limit=7)["pids_limit"] == 7
