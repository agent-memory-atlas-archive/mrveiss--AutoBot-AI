# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A breaker reset by timeout must not keep reporting "open" in health (#18553)."""

import time
from types import SimpleNamespace

from autobot_shared.redis_management.connection_manager import RedisConnectionManager
from autobot_shared.redis_management.statistics import ConnectionMetrics


def _open_manager(age_s: float) -> RedisConnectionManager:
    m = object.__new__(RedisConnectionManager)
    m._config_broken = set()
    m._circuit_open = {"main": True}
    m._failure_counts = {"main": 5}
    m._last_failure_times = {"main": time.time() - age_s}
    m._pool_config = SimpleNamespace(circuit_breaker_timeout=60)
    m._states = {}
    m._metrics = {"main": ConnectionMetrics(circuit_breaker_state="open")}
    return m


def test_timeout_reset_clears_the_open_metric():
    m = _open_manager(age_s=120)
    assert m._check_circuit_breaker("main") is False
    assert m._metrics["main"].circuit_breaker_state != "open"


def test_still_open_before_timeout_keeps_the_open_metric():
    m = _open_manager(age_s=1)
    assert m._check_circuit_breaker("main") is True
    assert m._metrics["main"].circuit_breaker_state == "open"
