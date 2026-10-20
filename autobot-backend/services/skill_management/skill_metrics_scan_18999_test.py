# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""get_metrics reads error keys with ONE SCAN walk, not a blocking KEYS per day (#18999)."""

import fnmatch
from unittest.mock import AsyncMock

import pytest

from services.skill_management import skill_metrics as sm


class FakeRedis:
    def __init__(self, store):
        self.store = store
        self.walks = 0

    def keys(self, *a, **k):  # KEYS must never be issued
        raise AssertionError("KEYS issued")

    async def scan_iter(self, match=None, count=10):
        self.walks += 1
        for k in list(self.store):
            if fnmatch.fnmatchcase(k, match):
                yield k

    async def get(self, key):
        return self.store.get(key)

    async def lrange(self, *a):
        return []


@pytest.mark.asyncio
async def test_error_patterns_equal_per_day_walk_in_one_scan():
    from datetime import timedelta

    today = sm.now_utc().strftime("%Y-%m-%d")
    old = (sm.now_utc() - timedelta(days=2)).strftime("%Y-%m-%d")
    p = f"{sm.REDIS_SKILL_METRICS_PREFIX}s1:"
    store = {
        f"{p}{today}:total": "3", f"{p}{today}:success": "1",
        f"{p}{today}:error:Timeout": "2", f"{p}{old}:error:Timeout": "5",
        f"{p}{old}:error:Boom": "1", f"{p}1999-01-01:error:Ancient": "9",
    }  # fmt: skip
    r = FakeRedis(store)
    inst = sm.SkillMetrics()
    inst._get_redis = AsyncMock(return_value=r)
    out = await inst.get_metrics("s1", days=7)
    assert out["error_patterns"] == {"Timeout": 7, "Boom": 1}  # out-of-window day excluded
    # Breaks if the walk moves back inside the per-day loop (7 walks).
    assert r.walks == 1
