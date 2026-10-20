# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""One fake ``AsyncSession``, instead of thirty-odd hand-rolled ones (#18883).

Tests across both backends each wrote their own ``_Session`` with ``commit`` and ``refresh``.
They drifted from the real class: five lacked ``attribute_names`` on ``refresh``, five made it
keyword-only (the real one is positional-or-keyword), and one took ``with_for_update`` on
``get`` positionally (the real one is keyword-only). A fake whose signature differs from the
real session accepts calls the production code can never make, and rejects ones it does.

Rules:

1. **Signatures copy ``sqlalchemy.ext.asyncio.AsyncSession``** -- names, kinds and order.
   ``fake_async_session_test.py`` compares them with ``inspect.signature`` against the
   installed SQLAlchemy, so a SQLAlchemy upgrade that moves a parameter fails there.
2. **Recording is always on** (``added``, ``deleted``, ``statements``, ``refreshed`` and the
   commit/flush/rollback counts); behaviour is opt-in through the constructor. A test that
   passes nothing gets a harmless session that remembers what it was asked.
3. **No SQL is interpreted.** Statements are recorded and answered from ``results``; to test
   a real query, use a real session.

Usage::

    from testkit.fake_async_session import FakeAsyncSession, FakeResult

    session = FakeAsyncSession(assign_ids=True, results=[FakeResult([row])])
    await service.create(session, payload)
    assert session.added and session.commits == 1
"""

from __future__ import annotations

import itertools
from collections import deque
from typing import Any, Callable, Iterable, Optional

from testkit.fake_result import FakeResult

__all__ = ["FakeAsyncSession", "FakeResult"]


class _FakeTransaction:
    """What ``begin`` / ``begin_nested`` return: an async context manager that does nothing."""

    async def __aenter__(self) -> "_FakeTransaction":
        return self

    async def __aexit__(self, type_: Any, value: Any, traceback: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _as_result(item: Any, stmt: Any) -> FakeResult:
    """Turn one queued answer (result, rows or callable) into a ``FakeResult``."""
    if isinstance(item, BaseException):
        raise item
    if callable(item) and not isinstance(item, FakeResult):
        item = item(stmt)
    if isinstance(item, FakeResult):
        return item
    return FakeResult(list(item) if item is not None else [])


class FakeAsyncSession:
    """Records every call; answers ``execute`` / ``get`` / ``commit`` only as told to."""

    def __init__(
        self,
        *,
        # True numbers new objects 1, 2, ...; a zero-argument callable (e.g. ``uuid.uuid4``) mints each id.
        assign_ids: Any = False,
        results: Optional[Iterable[Any]] = None,
        default_result: Any = None,
        rows: Optional[dict] = None,
        commit_error: Any = None,
        on_refresh: Optional[Callable[[Any], None]] = None,
        on_get: Optional[Callable[[Any, Any], Any]] = None,
    ) -> None:
        self.assign_ids = assign_ids
        self.results: deque = deque(results or ())
        self.default_result = default_result
        self.rows: dict = {} if rows is None else rows
        self.commit_error = commit_error
        self.on_refresh = on_refresh
        self.on_get = on_get
        self.added: list = []
        self.deleted: list = []
        self._expunged: list = []  # detached pending instances flush must skip
        self.statements: list = []
        self.refreshed: list = []
        self.commits = 0
        self.flushes = 0
        self.rollbacks = 0
        self.closed = False
        self._last_commit_failed = False
        self._ids = itertools.count(1)

    @property
    def committed(self) -> bool:
        """True once a commit has succeeded."""
        return self.commits > 0 and not self._last_commit_failed

    def _register(self, instance: object) -> None:
        pk = getattr(instance, "id", None)
        if pk is not None:
            self.rows[(type(instance), pk)] = instance

    def _assign(self, instance: object) -> None:
        if self.assign_ids and getattr(instance, "id", None) is None:
            make = self.assign_ids if callable(self.assign_ids) else self._ids.__next__
            instance.id = make()
        self._register(instance)

    def add(self, instance: object, _warn: bool = True) -> None:
        self.added.append(instance)
        # Re-adding an expunged instance makes it pending again, as on a real session.
        self._expunged = [gone for gone in self._expunged if gone is not instance]
        self._register(instance)

    def add_all(self, instances: Iterable[object]) -> None:
        for instance in instances:
            self.add(instance)

    async def flush(self, objects: Optional[Iterable[Any]] = None) -> None:
        self.flushes += 1
        for instance in self.added:
            if any(instance is gone for gone in [*self.deleted, *self._expunged]):
                continue
            self._assign(instance)

    def _commit_failure(self) -> Optional[BaseException]:
        err = self.commit_error
        if callable(err) and not isinstance(err, BaseException):
            err = err(self.commits)
        return err

    async def commit(self) -> None:
        self.commits += 1
        err = self._commit_failure()
        self._last_commit_failed = err is not None
        if err is not None:
            raise err
        await self.flush()

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def close(self) -> None:
        self.closed = True

    async def refresh(
        self,
        instance: object,
        attribute_names: Optional[Iterable[str]] = None,
        with_for_update: Any = None,
    ) -> None:
        self.refreshed.append(instance)
        self._assign(instance)
        if self.on_refresh is not None:
            self.on_refresh(instance)

    async def execute(
        self,
        statement: Any,
        params: Any = None,
        *,
        execution_options: Any = None,
        bind_arguments: Any = None,
        **kw: Any,
    ) -> FakeResult:
        self.statements.append(statement)
        if self.results:
            return _as_result(self.results.popleft(), statement)
        return _as_result(self.default_result, statement)

    async def scalar(
        self,
        statement: Any,
        params: Any = None,
        *,
        execution_options: Any = None,
        bind_arguments: Any = None,
        **kw: Any,
    ) -> Any:
        result = await self.execute(statement, params, execution_options=execution_options, **kw)
        return result.scalar()

    async def scalars(
        self,
        statement: Any,
        params: Any = None,
        *,
        execution_options: Any = None,
        bind_arguments: Any = None,
        **kw: Any,
    ) -> Any:
        result = await self.execute(statement, params, execution_options=execution_options, **kw)
        return result.scalars()

    async def get(
        self,
        entity: Any,
        ident: Any,
        *,
        options: Any = None,
        populate_existing: bool = False,
        with_for_update: Any = None,
        identity_token: Any = None,
        execution_options: Any = None,
    ) -> Any:
        """Identity-map hit first, then ``on_get(entity, ident)``, else ``None``."""
        if (entity, ident) in self.rows:
            return self.rows[(entity, ident)]
        return self.on_get(entity, ident) if self.on_get is not None else None

    async def delete(self, instance: object) -> None:
        self.deleted.append(instance)
        pk = getattr(instance, "id", None)
        self.rows.pop((type(instance), pk), None)

    async def merge(self, instance: Any, *, load: bool = True, options: Any = None) -> Any:
        self.add(instance)
        return instance

    def expunge(self, instance: object) -> None:
        """Detach *instance*: an expunged pending object is transient, so no later flush registers it."""
        self.rows.pop((type(instance), getattr(instance, "id", None)), None)
        self._expunged.append(instance)

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction()

    def begin_nested(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def __aenter__(self) -> "FakeAsyncSession":
        return self

    async def __aexit__(self, type_: Any, value: Any, traceback: Any) -> None:
        await self.close()
