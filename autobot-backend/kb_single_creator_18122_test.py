# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Both knowledge-base accessors share ONE creator under ONE lock (#18122, slice S1).

Behavioural, with a stub ``knowledge_base.KnowledgeBase`` that counts constructions.
F2 = ``get_or_create_knowledge_base(app)``; F3 = ``get_knowledge_base_async()``.
Each test names the line whose reversion makes it fail.
"""

import asyncio
import sys
import time
import types
from types import SimpleNamespace

import pytest

import knowledge_factory as kf


class _Stub:
    """Counts constructions; ``outcome`` decides what ``initialize()`` returns."""

    constructed = 0
    outcome = True

    def __init__(self) -> None:
        type(self).constructed += 1
        self.initialized = False

    async def initialize(self) -> bool:
        await asyncio.sleep(0.01)  # a real yield point, so concurrent callers interleave
        self.initialized = bool(type(self).outcome)
        return type(self).outcome


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch):
    _Stub.constructed, _Stub.outcome = 0, True
    mod = types.ModuleType("knowledge_base")
    mod.KnowledgeBase = _Stub
    monkeypatch.setitem(sys.modules, "knowledge_base", mod)
    monkeypatch.setattr(kf, "_knowledge_base_instance", None)
    monkeypatch.setattr(kf, "_last_kb_init_failure", 0.0)
    monkeypatch.setattr(kf, "_knowledge_base_lock", asyncio.Lock())
    adopted = []
    monkeypatch.setattr(kf, "_adopt_legacy_facts", adopted.append)
    return adopted


def _app():
    return SimpleNamespace(state=SimpleNamespace())


async def test_concurrent_f2_and_f3_build_exactly_one_instance(_fresh_store):
    """(a) Reverting `async with _knowledge_base_lock:` in `_create_under_lock` -> 2 builds."""
    app = _app()
    via_app, via_async = await asyncio.gather(kf.get_or_create_knowledge_base(app), kf.get_knowledge_base_async())
    assert _Stub.constructed == 1
    assert via_app is via_async is app.state.knowledge_base is kf.peek_knowledge_base()
    # D7: adoption/backfill started once, on whichever path created first.
    assert _fresh_store == [via_app]


async def test_control_sequential_calls_also_build_one(_fresh_store):
    """(a) contrast: the stub does not over-count, so the gather test's `1` means something."""
    app = _app()
    first = await kf.get_knowledge_base_async()
    assert await kf.get_or_create_knowledge_base(app) is first
    assert _Stub.constructed == 1


async def test_f3_honours_the_cooldown_after_a_failed_init():
    """(b) Reverting the `_cooldown_remaining()` gate in `_create_under_lock` -> 2 builds."""
    _Stub.outcome = False
    assert await kf.get_knowledge_base_async() is None
    assert _Stub.constructed == 1
    assert await kf.get_knowledge_base_async() is None
    assert _Stub.constructed == 1, "a call inside the cooldown must not construct"


async def test_f3_retries_once_the_cooldown_has_elapsed():
    """(b) contrast: reverting `_last_kb_init_failure = time.monotonic()` in
    `_create_new_knowledge_base` -> the failure leaves no stamp and the backdating below is moot."""
    _Stub.outcome = False
    assert await kf.get_knowledge_base_async() is None
    kf._last_kb_init_failure = time.monotonic() - kf.KB_RETRY_COOLDOWN_SECONDS - 1
    _Stub.outcome = True
    kb = await kf.get_knowledge_base_async()
    assert _Stub.constructed == 2 and kb is not None and kb.initialized


async def test_force_refresh_bypasses_the_cooldown():
    """(b) Reverting `0.0 if force_refresh` in `_create_under_lock` -> /reinit is suppressed."""
    _Stub.outcome = False
    assert await kf.get_knowledge_base_async() is None
    _Stub.outcome = True
    assert await kf.get_or_create_knowledge_base(_app(), force_refresh=True) is not None
    assert _Stub.constructed == 2


async def test_uninitialized_singleton_is_never_returned_as_ready():
    """(c) Reverting the `_is_kb_initialized` test in `peek_knowledge_base` -> stale one returned."""
    stale = _Stub()
    kf._knowledge_base_instance = stale
    assert kf.peek_knowledge_base() is None
    fresh = await kf.get_knowledge_base_async()
    assert fresh is not stale and fresh.initialized


async def test_failed_init_returns_none_not_the_uninitialized_instance():
    """(c) contrast: reverting `if await kb.initialize():` in `_create_new_knowledge_base`
    (publishing unconditionally) would return and publish an `initialized=False` instance."""
    _Stub.outcome = False
    app = _app()
    assert await kf.get_knowledge_base_async() is None
    assert await kf.get_or_create_knowledge_base(app, force_refresh=True) is None
    assert kf.peek_knowledge_base() is None
    assert getattr(app.state, "knowledge_base", None) is None
