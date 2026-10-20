# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A heartbeat-reported service name that is not a unit name never reaches Ansible (#19005).

Two layers, driven on the real reconciler (loaded from disk: the package conftest stubs
`services.*`, and a plain import would yield a MagicMock that passes every check):

* ingest: `_upsert_service` drops a hostile name and stores a real unit name;
* sink: `_remediate_failed_services` skips a hostile-named failed row that was stored before the
  ingest check existed, so `execute_playbook` is never awaited for it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testkit.fake_async_session import FakeAsyncSession

_SLM_ROOT = Path(__file__).resolve().parent.parent
if str(_SLM_ROOT) not in sys.path:
    sys.path.insert(0, str(_SLM_ROOT))


def _load_real_reconciler():
    spec = importlib.util.spec_from_file_location(
        "reconciler_under_hostile_names_19005_test", _SLM_ROOT / "services" / "reconciler.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconciler_under_hostile_names_19005_test"] = module
    spec.loader.exec_module(module)
    return module


reconciler = _load_real_reconciler()

_HOSTILE = [
    "{{ lookup('pipe', 'id') }}",
    "a b",
    'x"y',
    "x'y",
    "a;b",
    "a\nb",
    "-H",
    "x" * 300,
    "../x",
    "redis\u2010stack",
    "r\u00e9dis",
    123,
]
_VALID = "redis-stack.service"


class _NestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _upsert_db() -> AsyncMock:
    db = AsyncMock()
    db.begin_nested = MagicMock(return_value=_NestedTransaction())
    found = MagicMock()
    found.scalar_one_or_none.return_value = None  # no existing row: the name is created
    db.execute = AsyncMock(return_value=found)
    db.add = MagicMock()
    return db


def _new_service(service: reconciler.ReconcilerService) -> None:
    service._create_new_service = lambda _node_id, name, *_a, **_kw: SimpleNamespace(service_name=name)


@pytest.mark.parametrize("name", _HOSTILE, ids=lambda n: repr(n)[:30])
def test_the_ingest_drops_a_hostile_name_and_touches_nothing(name):
    svc = reconciler.ReconcilerService()
    _new_service(svc)
    db = _upsert_db()

    asyncio.run(svc._upsert_service(db, "n-1", {"name": name, "status": "failed"}, datetime.now(timezone.utc)))

    db.execute.assert_not_awaited()
    db.add.assert_not_called()


def test_the_ingest_stores_a_real_unit_name():
    svc = reconciler.ReconcilerService()
    _new_service(svc)
    db = _upsert_db()

    asyncio.run(svc._upsert_service(db, "n-1", {"name": _VALID, "status": "running"}, datetime.now(timezone.utc)))

    db.add.assert_called_once()
    assert db.add.call_args.args[0].service_name == _VALID


def test_the_warning_names_the_node_and_carries_only_a_capped_repr_of_the_name(caplog):
    svc = reconciler.ReconcilerService()
    db = _upsert_db()
    hostile = "evil\n" + "x" * 500

    with caplog.at_level("WARNING"):
        asyncio.run(svc._upsert_service(db, "n-7", {"name": hostile}, datetime.now(timezone.utc)))

    (record,) = [r for r in caplog.records if "invalid unit name" in r.getMessage()]
    message = record.getMessage()
    assert "n-7" in message
    assert "\n" not in message, "a raw newline in a logged name forges log lines"
    assert len(message) < 250, "the logged name must be length-capped"


class _LoopSession:
    """`db_service.session()` stand-in: the Setting check, the Service list, one Node lookup per node-bearing row."""

    def __init__(self, services, nodes):
        self._db = FakeAsyncSession(results=[[SimpleNamespace(value="true")], list(services), *[[n] for n in nodes]])

    @asynccontextmanager
    async def session(self):
        yield self._db


def _failed_row(name: str) -> SimpleNamespace:
    return SimpleNamespace(node_id="node-1", service_name=name)


def _run_remediation(rows, nodes):
    execute_playbook = AsyncMock(return_value={"success": True, "output": "ok", "returncode": 0, "timed_out": False})
    executor = SimpleNamespace(execute_playbook=execute_playbook)
    svc = reconciler.ReconcilerService()
    svc._is_remediation_suppressed = AsyncMock(return_value=False)

    async def _remediate(_db, node, service):
        await svc._restart_service_via_ansible(node.node_id, service.service_name, timeout_s=1)
        return True

    svc._remediate_failed_service = _remediate
    fake_module = SimpleNamespace(get_playbook_executor=lambda: executor)
    with (
        patch.dict(sys.modules, {"services.database": SimpleNamespace(db_service=_LoopSession(rows, nodes))}),
        patch.dict(sys.modules, {"services.playbook_executor": fake_module}),
    ):
        asyncio.run(svc._remediate_failed_services())
    return execute_playbook


@pytest.mark.parametrize("name", [h for h in _HOSTILE if isinstance(h, str)], ids=lambda n: repr(n)[:30])
def test_a_stored_hostile_failed_row_never_reaches_execute_playbook(name):
    execute_playbook = _run_remediation([_failed_row(name)], [])

    execute_playbook.assert_not_awaited()


def test_a_stored_valid_failed_row_still_reaches_execute_playbook():
    node = SimpleNamespace(node_id="node-1", status="online")

    execute_playbook = _run_remediation([_failed_row("autobot-backend")], [node])

    execute_playbook.assert_awaited_once()
    assert execute_playbook.await_args.kwargs["extra_vars"]["service_name"] == "autobot-backend"


def test_a_hostile_row_does_not_stop_the_valid_row_after_it():
    node = SimpleNamespace(node_id="node-1", status="online")

    execute_playbook = _run_remediation([_failed_row("a b"), _failed_row("autobot-backend")], [node])

    execute_playbook.assert_awaited_once()
