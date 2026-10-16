# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""ResourceMonitor reads the CANONICAL Redis breaker, not a process-local one (#18553).

The old reader asked ``utils.redis_immediate_connection.redis_circuit_breaker``,
which nothing ever fed, so ``redis_available`` was always True. The fake below
is the canonical ``get_redis_health()`` payload shape.
"""

import asyncio
from unittest.mock import patch

from utils.async_cancellation import ResourceMonitor

_HEALTH = "autobot_shared.redis_client.get_redis_health"


def _health(main_state: str) -> dict:
    return {"databases": {"main": {"state": "failed", "metrics": {"circuit_breaker_state": main_state}}}}


def _redis_available() -> bool:
    return asyncio.run(ResourceMonitor().check_resources())["redis"]


def test_open_canonical_breaker_means_redis_unavailable():
    with patch(_HEALTH, return_value=_health("open")):
        assert _redis_available() is False


def test_closed_canonical_breaker_means_redis_available():
    with patch(_HEALTH, return_value=_health("closed")):
        assert _redis_available() is True


def test_other_database_breaker_does_not_flip_main():
    health = {"databases": {"knowledge": {"metrics": {"circuit_breaker_state": "open"}}}}
    with patch(_HEALTH, return_value=health):
        assert _redis_available() is True


def test_unreadable_health_keeps_the_prior_unavailable_fallback():
    with patch(_HEALTH, side_effect=RuntimeError("manager down")):
        assert _redis_available() is False
