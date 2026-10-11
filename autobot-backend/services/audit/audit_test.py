# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The canonical audit service records and answers every audit question (#17225).

Covers the write path (every producer lands its own category and action), the
cross-thread hand-off, the read filters, and the one retention path.
"""

import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.audit import audit
from services.audit.projection import UNIFIED_KEY, category_key, read_events
from services.audit.purge import purge_expired
from services.audit.retention import RETENTION_DAYS, ttl_seconds
from services.audit.taxonomy import AuditAction, AuditCategory, AuditEvent, AuditFilter, EventType

_DAY = 24 * 3600


def _event(category=AuditCategory.SECURITY, action="x", actor="alice", at=None, **kw) -> AuditEvent:
    return AuditEvent(category=category, action=action, actor_id=actor, occurred_at=at or time.time(), **kw)


def _captured(run_redis_write):
    """The coroutine handed to the patched ``run_redis_write``."""
    return run_redis_write.call_args.args[0]


async def _record_of(emit_call) -> AuditEvent:
    """Run *emit_call* and return the event that reached ``record``."""
    seen = []

    async def fake_record(event):
        seen.append(event)

    with (
        patch.object(audit, "record", side_effect=fake_record),
        patch.object(audit, "run_redis_write") as rrw,
    ):
        emit_call()
        await _captured(rrw)
    return seen[0]


class TestEveryProducerRecordsItsOwnEvent:
    @pytest.mark.asyncio
    async def test_emit_of_an_audit_event_keeps_its_category_and_action(self) -> None:
        """#17225 regression: `emit` was shadowed by an EventType bridge, so this was COMPLIANCE/repr."""
        ev = _event(category=AuditCategory.GOVERNANCE, action="task.claim")
        got = await _record_of(lambda: audit.emit(ev))
        assert got is ev
        assert got.category is AuditCategory.GOVERNANCE and got.action == "task.claim"

    @pytest.mark.asyncio
    async def test_emit_compliance_takes_an_event_type(self) -> None:
        got = await _record_of(lambda: audit.emit_compliance(EventType.USER_LOGIN, "bob", ip_address="ip"))
        assert (got.category, got.action, got.actor_id) == (AuditCategory.COMPLIANCE, "user.login", "bob")

    @pytest.mark.asyncio
    async def test_emit_compliance_without_an_actor_is_the_system(self) -> None:
        got = await _record_of(lambda: audit.emit_compliance("config.changed", None))
        assert got.actor_id == "system"

    @pytest.mark.asyncio
    async def test_audit_record_is_a_security_event(self) -> None:
        got = await _record_of(lambda: audit.audit_record("carol", AuditAction.API_KEY_CREATE, outcome="denied"))
        assert (got.category, got.action, got.actor_id, got.outcome) == (
            AuditCategory.SECURITY,
            "api_key.create",
            "carol",
            "denied",
        )


class TestCrossThreadHandOff:
    @pytest.mark.asyncio
    async def test_a_thread_without_a_loop_hands_its_write_to_the_serving_loop(self) -> None:
        recorded = asyncio.Event()

        async def fake_record(event):
            recorded.set()

        with patch.object(audit, "record", side_effect=fake_record):
            audit._remember_loop()
            results = []
            worker = threading.Thread(target=lambda: results.append(audit.emit(_event())))
            worker.start()
            worker.join()
            await asyncio.wait_for(recorded.wait(), timeout=2)
        assert results == [True]

    def test_nothing_to_hand_to_is_reported_not_silent(self) -> None:
        with patch.object(audit, "_LOOP", None), patch.object(audit, "record", new=AsyncMock()):
            assert audit.emit(_event()) is False


class TestProjectionWrite:
    @pytest.mark.asyncio
    async def test_record_writes_unified_and_category_with_the_table_ttl(self) -> None:
        pipe = MagicMock()
        pipe.zadd = AsyncMock()
        pipe.expire = AsyncMock()
        pipe.execute = AsyncMock()
        redis = MagicMock()
        redis.pipeline.return_value.__aenter__ = AsyncMock(return_value=pipe)
        redis.pipeline.return_value.__aexit__ = AsyncMock(return_value=False)
        ev = _event(category=AuditCategory.KNOWLEDGE)
        with (
            patch.object(audit, "get_async_redis_client", new=AsyncMock(return_value=redis)),
            patch.object(audit, "_analyse_for_threats", new=AsyncMock()),
        ):
            await audit.record(ev)
        keys = [c.args[0] for c in pipe.zadd.call_args_list]
        assert keys == [UNIFIED_KEY, category_key(AuditCategory.KNOWLEDGE)]
        pipe.expire.assert_awaited_once_with(
            category_key(AuditCategory.KNOWLEDGE), ttl_seconds(AuditCategory.KNOWLEDGE)
        )

    @pytest.mark.asyncio
    async def test_threat_analysis_runs_even_when_redis_is_down(self) -> None:
        analyse = AsyncMock()
        with (
            patch.object(audit, "get_async_redis_client", new=AsyncMock(return_value=None)),
            patch.object(audit, "_analyse_for_threats", new=analyse),
        ):
            await audit.record(_event())
        analyse.assert_awaited_once()


def _stored(*events: AuditEvent) -> list[str]:
    return [json.dumps(e.to_dict()) for e in events]


class TestReadFilters:
    @pytest.mark.asyncio
    async def test_one_query_answers_user_in_window_across_categories(self) -> None:
        now = time.time()
        redis = MagicMock()
        redis.zrevrangebyscore = AsyncMock(
            return_value=_stored(
                _event(AuditCategory.SECURITY, "a", "u", now - 10),
                _event(AuditCategory.KNOWLEDGE, "b", "u", now - 5),
                _event(AuditCategory.COMPLIANCE, "c", "someone-else", now - 1),
            )
        )
        got = await read_events(redis, AuditFilter(actor_id="u", from_ts=now - 60, to_ts=now), max_scan=50)
        assert [e["action"] for e in got] == ["b", "a"], "newest first, only user u"
        redis.zrevrangebyscore.assert_awaited_once_with(UNIFIED_KEY, now, now - 60, start=0, num=50)

    def test_filter_fields_are_anded(self) -> None:
        ev = _event(resource_type="fact", resource_id="f1", metadata={"organization_id": "o1"}).to_dict()
        assert AuditFilter(resource_type="fact", resource_id="f1", metadata=(("organization_id", "o1"),)).matches(ev)
        assert not AuditFilter(resource_type="fact", resource_id="f2").matches(ev)
        assert not AuditFilter(metadata=(("organization_id", "o2"),)).matches(ev)
        assert AuditFilter(actions=frozenset({"x", "y"})).matches(ev)
        assert not AuditFilter(actions=frozenset({"y"})).matches(ev)

    @pytest.mark.asyncio
    async def test_an_unreachable_store_is_an_error_not_an_empty_trail(self) -> None:
        """#13258: "nothing recorded" and "cannot tell you" must not both be []."""
        with patch.object(audit, "get_async_redis_client", new=AsyncMock(return_value=None)):
            with pytest.raises(audit.AuditStoreUnavailable):
                await audit.query(actor_id="u")


def _purge_redis(unified: list[str]) -> MagicMock:
    redis = MagicMock()
    redis.zremrangebyscore = AsyncMock(return_value=1)
    redis.zcount = AsyncMock(return_value=1)
    redis.zrangebyscore = AsyncMock(return_value=unified)
    redis.zrem = AsyncMock()
    return redis


class TestOnePurgePath:
    @pytest.mark.asyncio
    async def test_each_category_is_purged_at_its_own_period(self) -> None:
        now = 1_000_000_000.0
        redis = _purge_redis([])
        await purge_expired(redis, now)
        cutoffs = {c.args[0]: c.args[2] for c in redis.zremrangebyscore.call_args_list}
        for category, days in RETENTION_DAYS.items():
            assert cutoffs[category_key(category)] == now - days * _DAY

    @pytest.mark.asyncio
    async def test_a_cap_only_shortens(self) -> None:
        now = 1_000_000_000.0
        redis = _purge_redis([])
        await purge_expired(redis, now, cap_days=30)
        cutoffs = {c.args[0]: c.args[2] for c in redis.zremrangebyscore.call_args_list}
        assert set(cutoffs.values()) == {now - 30 * _DAY}
        redis.zremrangebyscore.reset_mock()
        await purge_expired(redis, now, cap_days=10_000)
        cutoffs = {c.args[0]: c.args[2] for c in redis.zremrangebyscore.call_args_list}
        assert cutoffs[category_key(AuditCategory.SECURITY)] == now - RETENTION_DAYS[AuditCategory.SECURITY] * _DAY

    @pytest.mark.asyncio
    async def test_the_unified_key_judges_each_member_by_its_category(self) -> None:
        now = 1_000_000_000.0
        age = (RETENTION_DAYS[AuditCategory.SECURITY] + 1) * _DAY
        old_security = _event(AuditCategory.SECURITY, at=now - age)
        old_knowledge = _event(AuditCategory.KNOWLEDGE, at=now - age)
        assert RETENTION_DAYS[AuditCategory.KNOWLEDGE] * _DAY > age, "precondition: knowledge outlives it"
        raws = _stored(old_security, old_knowledge)
        redis = _purge_redis(raws)
        result = await purge_expired(redis, now)
        redis.zrem.assert_awaited_once_with(UNIFIED_KEY, raws[0])
        assert result.errors == 0

    @pytest.mark.asyncio
    async def test_dry_run_counts_and_removes_nothing(self) -> None:
        redis = _purge_redis(_stored(_event(at=1.0)))
        result = await purge_expired(redis, 1_000_000_000.0, dry_run=True)
        redis.zremrangebyscore.assert_not_called()
        redis.zrem.assert_not_called()
        assert result.deleted == len(AuditCategory) + 1
