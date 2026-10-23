# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Resource dwell and the DEGRADED reason, in isolation (#19292).

Loaded from disk, as the reconciler tests are: the package conftest stubs
``services.*`` names, and a stub would satisfy every check here while
exercising nothing.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

_SLM_ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "node_health_status_under_19292_test", _SLM_ROOT / "services" / "node_health_status.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


nhs = _load()

_HIGH = float(nhs.RESOURCE_DEGRADED_PCT + 1)  # degraded, below error
_SPIKE = float(nhs.RESOURCE_ERROR_PCT + 1)
_CALM = float(nhs.RESOURCE_DEGRADED_PCT - 10)


def _node(**kw):
    return SimpleNamespace(extra_data=kw.pop("extra_data", {}), **kw)


def _feed(node, cpu_values) -> list[str]:
    """Run samples through the persisted dwell; returns the damped level after each."""
    return [nhs.advance_resource_dwell(node, nhs.classify_resource_sample(cpu, 10.0, 10.0)) for cpu in cpu_values]


def test_the_real_module_was_loaded():
    assert inspect.isfunction(nhs.next_resource_state)
    assert nhs.RESOURCE_ENTER_SAMPLES >= 1 and nhs.RESOURCE_EXIT_SAMPLES >= 1


def test_classify_uses_the_worst_metric():
    assert nhs.classify_resource_sample(_CALM, _CALM, _CALM) == nhs.LEVEL_OK
    assert nhs.classify_resource_sample(_CALM, _HIGH, _CALM) == nhs.LEVEL_DEGRADED
    assert nhs.classify_resource_sample(_CALM, _CALM, _SPIKE) == nhs.LEVEL_ERROR


def test_a_single_spike_does_not_change_the_level():
    node = _node()
    levels = _feed(node, [_SPIKE, _CALM, _CALM])
    assert levels == [nhs.LEVEL_OK] * 3


def test_a_breach_one_sample_short_of_the_dwell_does_not_change_the_level():
    node = _node()
    levels = _feed(node, [_HIGH] * (nhs.RESOURCE_ENTER_SAMPLES - 1) + [_CALM])
    assert set(levels) == {nhs.LEVEL_OK}


def test_a_sustained_breach_changes_the_level():
    node = _node()
    levels = _feed(node, [_HIGH] * nhs.RESOURCE_ENTER_SAMPLES)
    assert levels[-1] == nhs.LEVEL_DEGRADED
    assert set(levels[:-1]) == {nhs.LEVEL_OK}


def test_a_breach_alternating_degraded_and_error_enters_the_milder_level():
    node = _node()
    run = [_HIGH if i % 2 else _SPIKE for i in range(nhs.RESOURCE_ENTER_SAMPLES)]
    assert _feed(node, run)[-1] == nhs.LEVEL_DEGRADED


def test_recovery_is_damped_too():
    node = _node()
    _feed(node, [_SPIKE] * nhs.RESOURCE_ENTER_SAMPLES)
    calm = _feed(node, [_CALM] * nhs.RESOURCE_EXIT_SAMPLES)
    assert set(calm[:-1]) == {nhs.LEVEL_ERROR}, "one calm sample must not clear a sustained breach"
    assert calm[-1] == nhs.LEVEL_OK


def test_a_relapse_during_recovery_restarts_the_exit_count():
    node = _node()
    _feed(node, [_HIGH] * nhs.RESOURCE_ENTER_SAMPLES)
    almost = [_CALM] * (nhs.RESOURCE_EXIT_SAMPLES - 1)
    levels = _feed(node, almost + [_HIGH] + almost)
    assert set(levels) == {nhs.LEVEL_DEGRADED}


def test_dwell_state_lives_on_the_node_and_keeps_other_extra_data():
    node = _node(extra_data={"gpu": "kept"})
    _feed(node, [_HIGH])
    assert node.extra_data["gpu"] == "kept"
    assert node.extra_data[nhs.HEALTH_STATE_KEY]["streak"] == 1


def test_status_reason_per_path():
    assert nhs.status_level_and_reason(nhs.LEVEL_ERROR, False) == (nhs.LEVEL_ERROR, nhs.REASON_RESOURCE_PRESSURE)
    assert nhs.status_level_and_reason(nhs.LEVEL_DEGRADED, False) == (
        nhs.LEVEL_DEGRADED,
        nhs.REASON_RESOURCE_PRESSURE,
    )
    assert nhs.status_level_and_reason(nhs.LEVEL_OK, True) == (nhs.LEVEL_DEGRADED, nhs.REASON_SERVICE_HEALTH)
    assert nhs.status_level_and_reason(nhs.LEVEL_DEGRADED, True) == (nhs.LEVEL_DEGRADED, nhs.REASON_SERVICE_HEALTH)
    assert nhs.status_level_and_reason(nhs.LEVEL_OK, False) == (nhs.LEVEL_OK, None)


def test_only_no_heartbeat_selects_an_agent_restart():
    now = datetime.now(timezone.utc)
    for reason, expected in (
        (nhs.REASON_NO_HEARTBEAT, True),
        (nhs.REASON_RESOURCE_PRESSURE, False),
        (nhs.REASON_SERVICE_HEALTH, False),
    ):
        node = _node(last_heartbeat=now)
        nhs.set_degraded_reason(node, reason)
        assert nhs.needs_agent_restart(node, now, 90) is expected, reason


def test_a_reasonless_legacy_row_restarts_only_when_its_heartbeat_is_stale():
    now = datetime.now(timezone.utc)
    fresh = _node(last_heartbeat=now - timedelta(seconds=10))
    stale = _node(last_heartbeat=(now - timedelta(seconds=600)).replace(tzinfo=None))
    never = _node(last_heartbeat=None)
    assert nhs.needs_agent_restart(fresh, now, 90) is False
    assert nhs.needs_agent_restart(stale, now, 90) is True
    assert nhs.needs_agent_restart(never, now, 90) is True
