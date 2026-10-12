# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""One definition of the performance metrics dataclasses and monitor_performance (#18450)."""

import pytest

from utils import hardware_metrics
from utils.performance_monitoring import decorator, metrics

_NAMES = ("GPUMetrics", "NPUMetrics", "MultiModalMetrics", "SystemPerformanceMetrics", "ServicePerformanceMetrics")


@pytest.mark.parametrize("name", _NAMES)
def test_hardware_metrics_serves_the_canonical_dataclass(name):
    assert getattr(hardware_metrics, name) is getattr(metrics, name)


def test_monitor_performance_is_the_canonical_decorator():
    assert hardware_metrics.monitor_performance is decorator.monitor_performance


def test_hardware_monitor_redis_client_feeds_the_canonical_decorator(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(decorator, "_redis_client", None)
    decorator.set_redis_client(sentinel)
    assert decorator._redis_client is sentinel
