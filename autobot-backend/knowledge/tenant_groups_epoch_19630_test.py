# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A team-membership change reaches every worker's cached memberships through the ACL epoch (#19630).

The epoch lives in one fake Redis shared by every simulated worker; each worker owns its own
``tenant_groups._cache``. The team service is the real one, driven over a fake session.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cachetools import TTLCache

from autobot_shared.db_session import POST_COMMIT_CALLBACKS
from autobot_shared.user_management.base_service import TenantContext
from autobot_shared.user_management.team_service import TeamService
from knowledge import acl_epoch, tenant_groups

TEAM_T = uuid.UUID("77777777-7777-7777-7777-777777777777")
USER_U = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ORG_1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_2 = uuid.UUID("22222222-2222-2222-2222-222222222222")
_GET_USER_TEAMS = "autobot_shared.user_management.team_service.TeamService.get_user_teams"


class _FakeRedis:
    """The knowledge-database client: the one epoch counter every worker shares."""

    def __init__(self) -> None:
        self.epoch: int | None = None

    async def incr(self, _key: str) -> int:
        self.epoch = (self.epoch or 0) + 1
        return self.epoch

    async def get(self, _key: str):
        return None if self.epoch is None else str(self.epoch).encode()


@pytest.fixture
def redis_db():
    db = _FakeRedis()
    with patch.object(acl_epoch, "get_async_redis_client", AsyncMock(return_value=db)):
        yield db


def _worker() -> TTLCache:
    return TTLCache(maxsize=16, ttl=3600)


async def _groups_as(worker: TTLCache, memberships: dict, org_id=None) -> list[str]:
    """Resolve U's groups on *worker* against the live *memberships* table."""
    teams = AsyncMock(side_effect=lambda uid: [SimpleNamespace(id=t) for t in memberships[uid]])
    with patch.object(tenant_groups, "_cache", worker), patch(_GET_USER_TEAMS, teams):
        return await tenant_groups.tenant_group_ids(TenantContext(org_id=org_id, user_id=USER_U), MagicMock())


def _service(session, hook=acl_epoch.bump_acl_epoch_now) -> TeamService:
    """The real ``TeamService`` over *session*, with the persistence steps it is not testing stubbed."""
    service = TeamService(session, TenantContext(org_id=ORG_1), on_membership_change=hook)
    service.get_team = AsyncMock(return_value=SimpleNamespace(name="T"))
    service._get_membership = AsyncMock(return_value=SimpleNamespace(role="member"))
    service._validate_membership_request = AsyncMock()
    service._create_membership_instance = MagicMock(return_value=SimpleNamespace())
    service._audit_log = AsyncMock()
    return service


def _session(with_commit_scope: bool = True) -> MagicMock:
    session = MagicMock()
    session.info = {POST_COMMIT_CALLBACKS: []} if with_commit_scope else {}
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    return session


async def _commit(session) -> None:
    from autobot_shared.db_session import (  # noqa: PLC0415 -- the scope's own post-commit step
        _run_post_commit_callbacks,
    )

    await _run_post_commit_callbacks(session)


@pytest.mark.asyncio
async def test_removing_a_member_drops_the_cached_team_in_every_worker(redis_db):
    memberships = {USER_U: [TEAM_T]}
    worker_a, worker_b = _worker(), _worker()
    assert await _groups_as(worker_a, memberships) == [str(TEAM_T)]
    assert await _groups_as(worker_b, memberships) == [str(TEAM_T)]

    memberships[USER_U] = []
    session = _session()
    await _service(session).remove_member(TEAM_T, USER_U)
    await _commit(session)

    assert await _groups_as(worker_a, memberships) == []
    assert await _groups_as(worker_b, memberships) == []


@pytest.mark.asyncio
async def test_without_a_bump_the_cache_still_serves_the_removed_team(redis_db):
    """Control: the TTL alone leaves the removed member in the team, so the bump is what drops it."""
    memberships = {USER_U: [TEAM_T]}
    worker = _worker()
    await _groups_as(worker, memberships)
    memberships[USER_U] = []
    assert await _groups_as(worker, memberships) == [str(TEAM_T)]


@pytest.mark.asyncio
async def test_deleting_a_team_drops_the_cached_team_of_all_its_members(redis_db):
    other = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    memberships = {USER_U: [TEAM_T], other: [TEAM_T]}
    worker = _worker()
    for uid in (USER_U, other):
        with (
            patch.object(tenant_groups, "_cache", worker),
            patch(_GET_USER_TEAMS, AsyncMock(return_value=[SimpleNamespace(id=TEAM_T)])),
        ):
            assert await tenant_groups.tenant_group_ids(TenantContext(user_id=uid), MagicMock()) == [str(TEAM_T)]

    session = _session()
    await _service(session).delete_team(TEAM_T)
    await _commit(session)

    for uid in (USER_U, other):
        memberships[uid] = []
        with patch.object(tenant_groups, "_cache", worker), patch(_GET_USER_TEAMS, AsyncMock(return_value=[])):
            assert await tenant_groups.tenant_group_ids(TenantContext(user_id=uid), MagicMock()) == []


@pytest.mark.asyncio
async def test_adding_a_member_bumps_the_epoch_too(redis_db):
    session = _session()
    await _service(session).add_member(TEAM_T, USER_U)
    await _commit(session)
    assert redis_db.epoch == 1


@pytest.mark.asyncio
async def test_the_bump_runs_after_the_commit_not_before(redis_db):
    session = _session()
    await _service(session).remove_member(TEAM_T, USER_U)
    assert redis_db.epoch is None  # flushed, not committed: a reader could still re-cache the old teams
    await _commit(session)
    assert redis_db.epoch == 1


@pytest.mark.asyncio
async def test_two_writes_in_one_transaction_queue_one_bump(redis_db):
    session = _session()
    service = _service(session)
    await service.remove_member(TEAM_T, USER_U)
    await service.delete_team(TEAM_T)
    assert len(session.info[POST_COMMIT_CALLBACKS]) == 1


@pytest.mark.asyncio
async def test_with_no_commit_scope_the_bump_runs_at_once(redis_db):
    await _service(_session(with_commit_scope=False)).remove_member(TEAM_T, USER_U)
    assert redis_db.epoch == 1


@pytest.mark.asyncio
async def test_a_failed_bump_surfaces_to_the_writer():
    failing = AsyncMock(side_effect=acl_epoch.AclEpochBumpError("no redis"))
    with pytest.raises(acl_epoch.AclEpochBumpError):
        await _service(_session(with_commit_scope=False), hook=failing).remove_member(TEAM_T, USER_U)


@pytest.mark.asyncio
async def test_an_unreadable_epoch_re_resolves_and_caches_nothing():
    worker = _worker()
    teams = AsyncMock(return_value=[SimpleNamespace(id=TEAM_T)])
    with (
        patch.object(tenant_groups, "_cache", worker),
        patch(_GET_USER_TEAMS, teams),
        patch.object(tenant_groups, "read_acl_epoch", AsyncMock(return_value=None)),
    ):
        for _ in range(3):
            assert await tenant_groups.tenant_group_ids(TenantContext(user_id=USER_U), MagicMock()) == [str(TEAM_T)]
    assert teams.await_count == 3 and len(worker) == 0


@pytest.mark.asyncio
async def test_an_unreadable_epoch_never_serves_an_entry_cached_under_a_readable_one(redis_db):
    memberships = {USER_U: [TEAM_T]}
    worker = _worker()
    await _groups_as(worker, memberships)
    memberships[USER_U] = []
    with patch.object(tenant_groups, "read_acl_epoch", AsyncMock(return_value=None)):
        assert await _groups_as(worker, memberships) == []


@pytest.mark.asyncio
async def test_the_team_lookup_is_scoped_to_the_active_organisation():
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    await tenant_groups._lookup_team_ids(session, USER_U, ORG_1)
    statement = session.execute.await_args.args[0]
    assert ORG_1 in statement.compile().params.values()


@pytest.mark.asyncio
async def test_the_cache_key_carries_the_organisation(redis_db):
    worker = _worker()
    in_org_1 = AsyncMock(return_value=[SimpleNamespace(id=TEAM_T)])
    in_org_2 = AsyncMock(return_value=[])
    with patch.object(tenant_groups, "_cache", worker):
        with patch(_GET_USER_TEAMS, in_org_1):
            first = await tenant_groups.tenant_group_ids(TenantContext(org_id=ORG_1, user_id=USER_U), MagicMock())
        with patch(_GET_USER_TEAMS, in_org_2):
            second = await tenant_groups.tenant_group_ids(TenantContext(org_id=ORG_2, user_id=USER_U), MagicMock())
    assert first == [str(TEAM_T)] and second == []
    assert in_org_2.await_count == 1
