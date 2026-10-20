# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""``FakeAsyncSession`` mirrors the installed ``AsyncSession`` and its opt-ins work (#18883)."""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy.exc import MultipleResultsFound, NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from testkit.fake_async_session import FakeAsyncSession, FakeResult

REQUIRED_METHODS = (
    "add add_all flush commit rollback close refresh execute scalar scalars get delete merge "
    "expunge begin begin_nested __aenter__ __aexit__"
).split()


def _shape(func: object) -> list[tuple[str, object]]:
    return [(p.name, p.kind) for p in inspect.signature(func).parameters.values()]


def test_the_method_set_covers_the_documented_surface() -> None:
    missing = [n for n in REQUIRED_METHODS if not callable(getattr(FakeAsyncSession, n, None))]
    assert not missing, f"FakeAsyncSession lacks {missing}"


@pytest.mark.parametrize("name", REQUIRED_METHODS)
def test_signature_parity_with_the_real_session(name: str) -> None:
    fake, real = getattr(FakeAsyncSession, name), getattr(AsyncSession, name)
    assert _shape(fake) == _shape(real), name
    assert inspect.iscoroutinefunction(fake) == inspect.iscoroutinefunction(real), name


def test_every_public_method_the_real_session_shares_is_checked() -> None:
    """A new fake method that the real session also has must join the parity list."""
    shared = {
        n
        for n, v in vars(FakeAsyncSession).items()
        if callable(v) and (not n.startswith("_") or n in ("__aenter__", "__aexit__")) and hasattr(AsyncSession, n)
    }
    assert shared <= set(REQUIRED_METHODS), sorted(shared - set(REQUIRED_METHODS))


@pytest.mark.asyncio
async def test_recording_is_always_on() -> None:
    s = FakeAsyncSession()
    obj = object.__new__(type("M", (), {}))
    s.add(obj)
    await s.execute("stmt")
    await s.refresh(obj)
    await s.delete(obj)
    await s.flush()
    await s.rollback()
    await s.commit()
    assert (s.added, s.statements, s.refreshed, s.deleted) == ([obj], ["stmt"], [obj], [obj])
    assert (s.flushes, s.rollbacks, s.commits, s.committed) == (2, 1, 1, True)


@pytest.mark.asyncio
async def test_assign_ids_numbers_new_objects_on_flush_and_commit() -> None:
    class M:
        id = None

    a, b = M(), M()
    s = FakeAsyncSession(assign_ids=True)
    s.add_all([a, b])
    assert a.id is None
    await s.commit()
    assert (a.id, b.id) == (1, 2)
    assert FakeAsyncSession().added == []


@pytest.mark.asyncio
async def test_assign_ids_accepts_a_factory_for_non_integer_keys() -> None:
    class M:
        id = None

    a = M()
    s = FakeAsyncSession(assign_ids=lambda: "uuid-1")
    s.add(a)
    await s.flush()
    assert a.id == "uuid-1"


@pytest.mark.asyncio
async def test_results_queue_then_default_result() -> None:
    s = FakeAsyncSession(results=[FakeResult(["a"]), ["b"], lambda stmt: [stmt]], default_result=["z"])
    assert (await s.execute("q1")).all() == ["a"]
    assert (await s.execute("q2")).all() == ["b"]
    assert (await s.execute("q3")).all() == ["q3"]
    assert (await s.execute("q4")).all() == ["z"]
    assert await s.scalar("q5") == "z"
    assert (await s.scalars("q6")).all() == ["z"]
    assert (await FakeAsyncSession().execute("q")).all() == []


@pytest.mark.asyncio
async def test_a_queued_exception_is_raised() -> None:
    s = FakeAsyncSession(results=[RuntimeError("db down")])
    with pytest.raises(RuntimeError, match="db down"):
        await s.execute("q")


@pytest.mark.asyncio
async def test_rows_is_the_identity_map_for_get_and_add_registers() -> None:
    class M:
        def __init__(self, id):
            self.id = id

    seen = M(7)
    s = FakeAsyncSession(rows={(M, 7): seen})
    assert await s.get(M, 7) is seen
    assert await s.get(M, 8) is None
    fresh = M(9)
    s.add(fresh)
    assert await s.get(M, 9) is fresh
    await s.delete(fresh)
    assert await s.get(M, 9) is None


@pytest.mark.asyncio
async def test_on_get_answers_what_the_identity_map_does_not_hold() -> None:
    s = FakeAsyncSession(on_get=lambda entity, ident: (entity, ident))
    assert await s.get(int, 3) == (int, 3)


@pytest.mark.asyncio
async def test_commit_error_as_an_exception_or_a_callable_of_the_attempt_number() -> None:
    always = FakeAsyncSession(commit_error=ValueError("boom"))
    with pytest.raises(ValueError):
        await always.commit()
    assert always.commits == 1 and not always.committed

    second = FakeAsyncSession(commit_error=lambda n: RuntimeError("n2") if n == 2 else None)
    await second.commit()
    with pytest.raises(RuntimeError):
        await second.commit()
    await second.commit()
    assert second.commits == 3 and second.committed


@pytest.mark.asyncio
async def test_on_refresh_is_called_with_the_instance() -> None:
    class M:
        id = None

    seen = []
    s = FakeAsyncSession(on_refresh=seen.append)
    obj = M()
    await s.refresh(obj)
    assert seen == [obj]


@pytest.mark.asyncio
async def test_context_managers_and_transactions() -> None:
    async with FakeAsyncSession() as s:
        async with s.begin():
            pass
        async with s.begin_nested() as nested:
            await nested.rollback()
    assert s.closed


def test_fake_result_reads() -> None:
    assert FakeResult([1]).scalar_one() == 1
    assert FakeResult([]).scalar_one_or_none() is None
    assert FakeResult([]).scalar() is None and FakeResult([]).first() is None
    assert FakeResult([1, 2]).first() == 1 and FakeResult([1, 2]).scalars().all() == [1, 2]
    assert list(FakeResult([1, 2])) == [1, 2]
    with pytest.raises(NoResultFound):
        FakeResult([]).scalar_one()
    with pytest.raises(MultipleResultsFound):
        FakeResult([1, 2]).scalar_one_or_none()


@pytest.mark.asyncio
async def test_a_deleted_instance_is_not_re_registered_by_the_commit() -> None:
    """A real session does not hand back a row that was deleted and committed."""
    model = type("M", (), {})
    obj = model()
    obj.id = 7
    s = FakeAsyncSession()
    s.add(obj)
    await s.delete(obj)
    await s.commit()
    assert await s.get(model, 7) is None


@pytest.mark.asyncio
async def test_expunge_is_synchronous_and_drops_the_tracked_row() -> None:
    model = type("M", (), {})
    obj = model()
    obj.id = 3
    s = FakeAsyncSession()
    s.add(obj)
    s.expunge(obj)
    assert await s.get(model, 3) is None


@pytest.mark.asyncio
async def test_an_expunged_pending_instance_is_not_registered_by_a_later_commit() -> None:
    """add -> expunge -> commit: the instance is transient, so get() must not return it (CodeRabbit, #18933)."""
    model = type("M", (), {})
    obj = model()
    obj.id = 7
    s = FakeAsyncSession()
    s.add(obj)
    s.expunge(obj)
    await s.commit()
    assert await s.get(model, 7) is None
    assert obj in s.added  # the call record is kept; only the pending work is dropped


@pytest.mark.asyncio
async def test_contrast_re_adding_an_expunged_instance_makes_it_pending_again() -> None:
    model = type("M", (), {})
    obj = model()
    obj.id = 8
    s = FakeAsyncSession()
    s.add(obj)
    s.expunge(obj)
    s.add(obj)
    await s.commit()
    assert await s.get(model, 8) is obj
