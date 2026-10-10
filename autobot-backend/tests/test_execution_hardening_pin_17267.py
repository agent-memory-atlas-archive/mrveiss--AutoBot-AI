# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Every ``containers.run`` in the execution backends: full kwargs, before vs after (#17267).

Decision (c): the three keys of the non-behavioural subset are added and NOTHING else
changes. The expected dicts below are the literals the sites passed before the change;
the autospec'd collection rejects any kwarg ``containers.run`` does not accept.
"""

from __future__ import annotations

from unittest.mock import MagicMock, create_autospec, patch

import docker
import pytest
from docker.models.containers import ContainerCollection

import services.execution.docker_backend as db
from services.execution.base_backend import ExecutionTask
from services.execution.container_pool import WarmContainerPool

#: True once the backends take the (c) subset; the pre-change tree runs this file with False.
_HARDENED = True

_FLOOR = {"security_opt": ["no-new-privileges"], "cap_drop": ["ALL"]}


def _added() -> dict:
    if not _HARDENED:
        return {}
    from autobot_shared.container_hardening import CONTAINER_PIDS_LIMIT

    return {**_FLOOR, "pids_limit": CONTAINER_PIDS_LIMIT}


def _client():
    client = MagicMock(spec=docker.DockerClient)
    client.containers = create_autospec(ContainerCollection, instance=True)
    container = MagicMock()
    container.id = "cid"
    container.short_id = "cid"
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = b"ok"
    client.containers.run.return_value = container
    return client


def _backend(client):
    with patch.object(db.docker, "from_env", return_value=client):
        return db.DockerBackend(use_pool=False, snapshot_index=MagicMock())


@pytest.mark.asyncio
async def test_cold_start_run_kwargs() -> None:
    client = _client()
    task = ExecutionTask(task_id="t1", code="print(1)", language="python", env_vars={"AUTOBOT_X": "1"})
    await _backend(client)._execute_cold(task, "python:3.10-slim")
    args, kwargs = client.containers.run.call_args
    assert args[0] == "python:3.10-slim"
    assert kwargs == {
        "detach": True,
        "mem_limit": "512m",
        "cpus": 1.0,
        "environment": kwargs["environment"],
        "stdout": True,
        "stderr": True,
        "remove": False,
        **_added(),
    }
    assert "network_mode" not in kwargs and "read_only" not in kwargs and "volumes" not in kwargs


@pytest.mark.asyncio
async def test_snapshot_restore_run_kwargs() -> None:
    client = _client()
    backend = _backend(client)
    backend._snapshot_index.get.return_value = MagicMock(user_id="", image_name="snap:1", session_id="s")
    await backend.restore("snap-id", "anyone")
    args, kwargs = client.containers.run.call_args
    assert args == ("snap:1", "sleep infinity")
    assert kwargs == {"detach": True, "remove": False, **_added()}


@pytest.mark.asyncio
async def test_warm_pool_run_kwargs() -> None:
    client = _client()
    pool = WarmContainerPool(docker_client=client, image="python:3.10-slim", pool_size=1)
    await pool._create_warm_container()
    args, kwargs = client.containers.run.call_args
    assert args == ("python:3.10-slim", ["sleep", "infinity"])
    assert kwargs == {"detach": True, "remove": False, **_added()}


@pytest.mark.asyncio
async def test_warm_pool_caller_kwargs_survive_and_cannot_weaken_the_floor() -> None:
    if not _HARDENED:
        pytest.skip("floor not applied before the change")
    client = _client()
    caller = {"network_mode": "none", "cap_drop": [], "security_opt": []}
    pool = WarmContainerPool(docker_client=client, image="i", pool_size=1, container_kwargs=caller)
    await pool._create_warm_container()
    kwargs = client.containers.run.call_args.kwargs
    assert kwargs["network_mode"] == "none"
    assert kwargs["cap_drop"] == ["ALL"] and kwargs["security_opt"] == ["no-new-privileges"]
