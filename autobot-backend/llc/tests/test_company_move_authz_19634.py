# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Who may move a company, and how a move is asked for (#19634, #19589, #19593).

The owner's rule for any ``parent_org_id`` change, enforced in
``CompanyService.move`` so every caller inherits it:

1. owner/admin on the moved org;
2. a new parent the actor can see (admin or member), else not found -- #12233's rule;
3. leaving a parent (promotion included) also needs owner/admin on that parent.

#19589: an explicit ``"parent_org_id": null`` promotes, an absent field moves nothing.

SQLite harness (``test_llc_org_chart``), like ``test_ceo_heartbeat_15907``. The
two-session lock race needs Postgres row locks and lives in
``tests/migrations/test_company_move_cycle_lock_19593.py``; here the lock's *set*
and *order* are pinned.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from api.user_management.dependencies import get_current_user, require_org_context
from llc.api import companies
from llc.models.company import CompanyCreate, CompanyUpdate
from llc.models.enums import MembershipRole
from llc.services.authz import NotAuthorisedError
from llc.services.company import CompanyNotFoundError, CompanyService
from llc.services.company_ceo import CompanyCEOService
from llc.services.membership_service import MembershipService
from llc.tests import test_llc_org_chart as _org_chart_harness
from user_management.models.organization import Organization
from user_management.services import TenantContext

engine = _org_chart_harness.engine
session_factory = _org_chart_harness.session_factory

_ACTOR = uuid.uuid4()
_ORG_ID = uuid.uuid4()


async def _org(session_factory, name: str, *, parent=None, role=MembershipRole.OWNER) -> uuid.UUID:  # noqa: ANN001
    """Create an org; ``_ACTOR`` holds *role* in it (``None`` = no membership at all)."""
    async with session_factory() as session:
        org = await CompanyService(session).create(CompanyCreate(name=name, slug=name.lower(), parent_org_id=parent))
        if role is not None:
            await MembershipService().add_member(session, str(org.id), str(_ACTOR), role)
        await session.commit()
        return org.id


async def _update(session_factory, org_id: uuid.UUID, data: CompanyUpdate) -> None:  # noqa: ANN001
    async with session_factory() as session:
        await CompanyService(session).update(org_id, data, actor_user_id=_ACTOR)
        await session.commit()


async def _parent_of(session_factory, org_id: uuid.UUID) -> uuid.UUID | None:  # noqa: ANN001
    async with session_factory() as session:
        return (await session.get(Organization, org_id)).parent_org_id


# ---------------------------------------------------------------------------
# #19634 -- authorization, enforced in the service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_plain_member_of_the_moved_org_is_refused(session_factory):  # noqa: ANN001
    parent = await _org(session_factory, "Parent")
    org = await _org(session_factory, "Acme", role=MembershipRole.MEMBER)
    with pytest.raises(NotAuthorisedError):
        await _update(session_factory, org, CompanyUpdate(parent_org_id=parent))
    assert await _parent_of(session_factory, org) is None


@pytest.mark.asyncio
async def test_an_unseen_new_parent_is_not_found_so_no_cross_tenant_graft(session_factory):  # noqa: ANN001
    foreign = await _org(session_factory, "Foreign", role=None)
    org = await _org(session_factory, "Acme")
    with pytest.raises(CompanyNotFoundError):
        await _update(session_factory, org, CompanyUpdate(parent_org_id=foreign))
    assert await _parent_of(session_factory, org) is None


@pytest.mark.asyncio
async def test_a_plain_member_of_the_new_parent_may_attach_under_it(session_factory):  # noqa: ANN001
    parent = await _org(session_factory, "Parent", role=MembershipRole.MEMBER)
    org = await _org(session_factory, "Acme")
    await _update(session_factory, org, CompanyUpdate(parent_org_id=parent))
    assert await _parent_of(session_factory, org) == parent


@pytest.mark.asyncio
async def test_a_non_admin_of_the_current_parent_cannot_promote(session_factory):  # noqa: ANN001
    parent = await _org(session_factory, "Parent", role=MembershipRole.MEMBER)
    org = await _org(session_factory, "Acme", parent=parent)
    with pytest.raises(NotAuthorisedError):
        await _update(session_factory, org, CompanyUpdate(parent_org_id=None))
    assert await _parent_of(session_factory, org) == parent


@pytest.mark.asyncio
async def test_a_non_admin_of_the_current_parent_cannot_move_out_sideways(session_factory):  # noqa: ANN001
    parent = await _org(session_factory, "Parent", role=MembershipRole.MEMBER)
    other = await _org(session_factory, "Other")
    org = await _org(session_factory, "Acme", parent=parent)
    with pytest.raises(NotAuthorisedError):
        await _update(session_factory, org, CompanyUpdate(parent_org_id=other))
    assert await _parent_of(session_factory, org) == parent


@pytest.mark.asyncio
async def test_a_move_without_an_actor_is_refused(session_factory):  # noqa: ANN001
    parent = await _org(session_factory, "Parent")
    org = await _org(session_factory, "Acme")
    async with session_factory() as session:
        with pytest.raises(NotAuthorisedError):
            await CompanyService(session).move(org, parent, actor_user_id=None)


# ---------------------------------------------------------------------------
# #19589 -- explicit null promotes, absent moves nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_explicit_null_promotes_and_designates_a_ceo(session_factory):  # noqa: ANN001
    parent = await _org(session_factory, "Parent")
    org = await _org(session_factory, "Acme", parent=parent)
    async with session_factory() as session:
        assert await CompanyCEOService().designation(session, org) is None

    await _update(session_factory, org, CompanyUpdate.model_validate({"parent_org_id": None}))

    assert await _parent_of(session_factory, org) is None
    async with session_factory() as session:
        assert await CompanyCEOService().designation(session, org) is not None


@pytest.mark.asyncio
async def test_an_absent_parent_field_moves_nothing_and_needs_no_move_rights(session_factory):  # noqa: ANN001
    parent = await _org(session_factory, "Parent", role=None)
    org = await _org(session_factory, "Acme", parent=parent, role=None)
    await _update(session_factory, org, CompanyUpdate.model_validate({"name": "Renamed"}))
    assert await _parent_of(session_factory, org) == parent


def test_only_parent_org_id_tells_null_from_absent():
    assert "parent_org_id" in CompanyUpdate.model_validate({"parent_org_id": None}).model_fields_set
    assert "parent_org_id" not in CompanyUpdate.model_validate({"name": "x"}).model_fields_set


# ---------------------------------------------------------------------------
# #19593 -- the rows a move locks, and their order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_move_locks_itself_and_the_whole_new_parent_chain_in_one_pass(session_factory):  # noqa: ANN001
    top = await _org(session_factory, "Top")
    mid = await _org(session_factory, "Mid", parent=top)
    org = await _org(session_factory, "Acme")
    async with session_factory() as session:
        svc = CompanyService(session)
        calls: list[set[uuid.UUID]] = []
        real = svc._lock_ids

        async def _record(ids: set[uuid.UUID]) -> None:
            calls.append(set(ids))
            await real(ids)

        svc._lock_ids = _record
        await svc.move(org, mid, actor_user_id=_ACTOR)
    assert calls == [{org, mid, top}]


@pytest.mark.asyncio
async def test_the_lock_statement_takes_rows_for_update_in_id_order():
    session = MagicMock()
    session.execute = AsyncMock()
    await CompanyService(session)._lock_ids({uuid.uuid4(), uuid.uuid4()})
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "ORDER BY organizations.id" in sql
    assert sql.rstrip().endswith("FOR UPDATE")


# ---------------------------------------------------------------------------
# The route: actor and admin flag reach the service; refusal is a 403
# ---------------------------------------------------------------------------


def _app(svc) -> FastAPI:  # noqa: ANN001
    app = FastAPI()
    app.include_router(companies.router, prefix="/api/llc")
    app.dependency_overrides[companies._get_service] = lambda: svc
    app.dependency_overrides[get_current_user] = lambda: {"user_id": str(_ACTOR)}
    ctx = TenantContext(org_id=_ORG_ID, user_id=_ACTOR, is_platform_admin=False)
    app.dependency_overrides[require_org_context] = lambda: ctx
    return app


@pytest.mark.asyncio
async def test_the_route_answers_a_refused_move_with_403_and_rolls_back():
    svc = SimpleNamespace(session=AsyncMock(), update=AsyncMock(side_effect=NotAuthorisedError("no")))
    transport = httpx.ASGITransport(app=_app(svc))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(f"/api/llc/companies/{_ORG_ID}", json={"parent_org_id": None})
    assert resp.status_code == 403
    svc.session.rollback.assert_awaited_once()
    svc.session.commit.assert_not_called()
    _, body = svc.update.await_args.args
    assert "parent_org_id" in body.model_fields_set and body.parent_org_id is None
    assert svc.update.await_args.kwargs == {"actor_user_id": _ACTOR, "platform_admin": False}
