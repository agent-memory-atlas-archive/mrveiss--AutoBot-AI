# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Same-tick requests count separately; a silent fail-open warns, rate-limited (#18617)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest

from autobot_shared import rate_limiter as rl
from autobot_shared.rate_limiter import RateLimiter

CALLER_ID = "alice:203.0.113.9"
FROZEN = 1_700_000_000.1234


@pytest.fixture
def fake():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


def _patch_redis(client):
    return patch.object(rl, "get_async_redis_client", AsyncMock(return_value=client))


@pytest.mark.asyncio
async def test_same_tick_window_requests_count_separately(fake):
    lim = RateLimiter("t18617")
    with _patch_redis(fake), patch.object(rl.time, "time", return_value=FROZEN):
        results = [await lim.acquire_window(CALLER_ID, 5, 300) for _ in range(6)]
        assert results == [True] * 5 + [False]
        win_key = lim._redis_key(CALLER_ID, "window")
        assert await fake.zcard(win_key) == 5


@pytest.mark.asyncio
async def test_same_tick_hour_window_requests_count_separately(fake):
    lim = RateLimiter("t18617", requests_per_minute=3, requests_per_hour=100)
    with _patch_redis(fake), patch.object(rl.time, "time", return_value=FROZEN):
        results = [await lim.acquire(CALLER_ID) for _ in range(4)]
    assert results == [True] * 3 + [False]


@pytest.mark.asyncio
async def test_old_member_scheme_collapses_same_tick(fake):
    """Contrast: the pre-fix member (tostring(now)) undercounts, proving the test bites."""
    old = """
    redis.call('ZADD', KEYS[1], ARGV[1], tostring(tonumber(ARGV[1])))
    return redis.call('ZCARD', KEYS[1])
    """
    for _ in range(3):
        count = await fake.eval(old, 1, "k", FROZEN)
    assert count == 1


@pytest.mark.asyncio
async def test_seq_key_has_ttl(fake):
    lim = RateLimiter("t18617")
    with _patch_redis(fake), patch.object(rl.time, "time", return_value=FROZEN):
        await lim.acquire_window(CALLER_ID, 5, 300)
        seq_key = lim._redis_key(CALLER_ID, "window") + ":seq"
        assert 0 < await fake.ttl(seq_key) <= 600


@pytest.mark.asyncio
async def test_none_client_warns_once_within_interval_without_key(caplog):
    lim = RateLimiter("t18617", log_label="pw-change")
    mono = iter([1000.0 + i for i in range(20)])
    with _patch_redis(None), patch.object(rl.time, "monotonic", lambda: next(mono)):
        with caplog.at_level(logging.WARNING):
            results = [await lim.acquire_window(CALLER_ID, 5, 300) for _ in range(10)]
    assert all(results)
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1 and "t18617/pw-change" in warns[0]
    assert CALLER_ID not in caplog.text and "alice" not in caplog.text


@pytest.mark.asyncio
async def test_warning_repeats_after_interval(caplog):
    lim = RateLimiter("t18617")
    ticks = iter([0.0, rl.FAIL_OPEN_WARN_INTERVAL_SECONDS + 1])
    with _patch_redis(None), patch.object(rl.time, "monotonic", lambda: next(ticks)):
        with caplog.at_level(logging.WARNING):
            await lim.acquire_window(CALLER_ID, 5, 300)
            await lim.acquire_window(CALLER_ID, 5, 300)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


@pytest.mark.asyncio
async def test_redis_error_fails_open_with_one_label_only_warning(caplog):
    lim = RateLimiter("t18617")
    boom = AsyncMock()
    boom.eval.side_effect = ConnectionError(f"cannot reach {CALLER_ID}")
    with _patch_redis(boom), caplog.at_level(logging.WARNING):
        results = [await lim.acquire_window(CALLER_ID, 5, 300) for _ in range(5)]
    assert all(results)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert CALLER_ID not in caplog.text
