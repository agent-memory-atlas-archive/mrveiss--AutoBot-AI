# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""scan_keys/count_keys find what KEYS found, in O(keys/COUNT) calls with COUNT=1000 (#18999)."""

import fnmatch

import pytest

from autobot_shared import redis_scan


class CountingRedis:
    """Fake async client: honours MATCH/COUNT like SCAN and counts server round trips."""

    def __init__(self, keys):
        self.keys_ = list(keys)
        self.scan_calls = 0

    async def scan_iter(self, match=None, count=10):
        for i in range(0, len(self.keys_), count):
            self.scan_calls += 1  # one SCAN round trip per COUNT-sized page
            for k in self.keys_[i : i + count]:
                if match is None or fnmatch.fnmatchcase(k, match):
                    yield k


FIXTURE = [f"doc:{i}" for i in range(1569)] + [f"kb_cache:{i}" for i in range(40)] + ["other:1"]


@pytest.mark.asyncio
async def test_equivalent_to_pattern_filter():
    r = CountingRedis(FIXTURE)
    got = await redis_scan.scan_keys(r, "kb_cache:*")
    assert sorted(got) == sorted(k for k in FIXTURE if k.startswith("kb_cache:"))
    assert await redis_scan.count_keys(r, "doc:*") == 1569


@pytest.mark.asyncio
async def test_walk_is_two_calls_not_hundreds():
    # Breaks if `count=REDIS_SCAN_COUNT` is dropped from scan_iter: the default of 10
    # would make ceil(1610/10) = 161 calls per walk.
    r = CountingRedis(FIXTURE)
    await redis_scan.count_keys(r, "doc:*")
    assert r.scan_calls == 2
