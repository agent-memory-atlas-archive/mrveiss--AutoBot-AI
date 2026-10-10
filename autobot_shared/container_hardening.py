# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The one container-hardening constraint set for Docker clients (#17267).

Every client that creates a sandbox/workspace container takes its security kwargs
from `hardening_kwargs`, so the constraint set cannot drift between copies. What a
client may legitimately vary is an explicit named parameter; everything else
(``cap_drop=ALL``, ``no-new-privileges``, the CPU period) is fixed here.
"""

from __future__ import annotations

from typing import Any

from autobot_shared.env_utils import env_int

#: Process cap for hardened containers (Linux pids cgroup), so a fork-bomb cannot
#: exhaust host PIDs (GH#11059). One env-backed value shared by every client.
CONTAINER_PIDS_LIMIT: int = env_int("AUTOBOT_WORKSPACE_PIDS_LIMIT", 512)

#: CFS period paired with every ``cpu_quota`` (microseconds).
CPU_PERIOD_US = 100000


def baseline_hardening_kwargs(*, pids_limit: int | None = CONTAINER_PIDS_LIMIT) -> dict[str, Any]:
    """The non-behavioural subset: ``cap_drop=ALL``, ``no-new-privileges`` and a pids cap.

    Changes no network, filesystem, mount, memory or user behaviour, so a client that
    cannot yet take the full set (``hardening_kwargs``) still gets the privilege floor.
    ``pids_limit=None`` omits the cap.
    """
    kwargs: dict[str, Any] = {"security_opt": ["no-new-privileges"], "cap_drop": ["ALL"]}
    if pids_limit is not None:
        kwargs["pids_limit"] = pids_limit
    return kwargs


def hardening_kwargs(
    *,
    network_mode: str,
    read_only: bool,
    memory_limit: str,
    cpu_quota: int,
    pids_limit: int | None = None,
) -> dict[str, Any]:
    """Docker create/run kwargs enforcing the shared constraint set.

    Args:
        network_mode: ``"none"`` unless the caller is explicitly allowed network.
        read_only: root filesystem read-only (False only for persistent workspaces).
        memory_limit: Docker memory string; also used as the swap cap (no swap).
        cpu_quota: CFS quota in microseconds per ``CPU_PERIOD_US``.
        pids_limit: process cap; omitted from the kwargs when None.
    """
    kwargs: dict[str, Any] = {
        "network_mode": network_mode,
        "read_only": read_only,
        **baseline_hardening_kwargs(pids_limit=None),
        "cap_add": [],
        "mem_limit": memory_limit,
        "memswap_limit": memory_limit,
        "cpu_quota": cpu_quota,
        "cpu_period": CPU_PERIOD_US,
    }
    if pids_limit is not None:
        kwargs["pids_limit"] = pids_limit
    return kwargs
