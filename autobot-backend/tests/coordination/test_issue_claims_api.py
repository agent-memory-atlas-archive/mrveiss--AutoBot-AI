# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Issue claims through the API: holder bound to the authenticated principal (#18811).

Mounts the real ``api.coordination`` router (so its ``get_current_user`` gate is under test) with
only the credential stood in for. No ``X-Organization-Id`` is ever sent: the endpoints are install-wide.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from api import coordination
from api.user_management.dependencies import get_current_user
from autobot_shared.coordination import work_claims

try:
    import fakeredis
    import fakeredis.aioredis as fakeredis_async
except ImportError:  # pragma: no cover
    fakeredis_async = None
    fakeredis = None  # type: ignore[assignment]

BASE = "/api/coordination/issue-claims"


def _user(request: Request) -> dict:
    """Header-driven principal: ``X-Test-User`` (+ ``X-Test-Service``); none is anonymous."""
    name = request.headers.get("X-Test-User")
    if not name:
        raise HTTPException(401, "Authentication required")
    if request.headers.get("X-Test-Service"):
        return {"username": name, "role": "admin", "service": True}
    return {"username": name, "role": "user"}


@pytest_asyncio.fixture
async def redis(monkeypatch):
    if fakeredis_async is None:
        pytest.skip("fakeredis[lua] not installed")
    client = fakeredis_async.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)

    async def _client(database: str = "main"):
        return client

    monkeypatch.setattr(work_claims, "get_async_redis_client", _client)
    yield client
    await client.flushall()


@pytest.fixture
def http(redis):
    app = FastAPI()
    app.include_router(coordination.router, prefix="/api/coordination")
    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def as_user(name: str, **extra: str) -> dict:
    return {"X-Test-User": name, **extra}


def test_claim_is_granted_and_bound_to_the_caller(http):
    r = http.post(f"{BASE}/18811", json={"owner": "s1", "note": "api"}, headers=as_user("alice"))
    assert r.status_code == 200 and r.json()["granted"] is True
    assert r.json()["holder"]["agent_id"] == "alice/s1"


def test_second_user_claim_is_409_naming_the_holder(http):
    http.post(f"{BASE}/7", json={"owner": "s1"}, headers=as_user("alice"))
    r = http.post(f"{BASE}/7", json={"owner": "s1"}, headers=as_user("bob"))
    assert r.status_code == 409
    assert r.json()["detail"]["holder"]["agent_id"] == "alice/s1" and r.json()["detail"]["granted"] is False


def test_holder_releases_own_claim(http):
    http.post(f"{BASE}/7", json={"owner": "s1"}, headers=as_user("alice"))
    r = http.delete(f"{BASE}/7", params={"owner": "s1"}, headers=as_user("alice"))
    assert r.status_code == 200 and r.json()["released"] is True
    assert http.get(f"{BASE}/7", headers=as_user("bob")).json()["held"] is False


@pytest.mark.parametrize("owner", ["s1", "anything"])
def test_another_users_release_is_refused_and_the_claim_stays_held(http, owner):
    http.post(f"{BASE}/7", json={"owner": "s1"}, headers=as_user("alice"))
    r = http.delete(f"{BASE}/7", params={"owner": owner}, headers=as_user("bob"))
    assert r.status_code == 409 and r.json()["detail"]["released"] is False
    held = http.get(f"{BASE}/7", headers=as_user("bob")).json()
    assert held["held"] is True and held["holder"]["agent_id"] == "alice/s1"


def test_same_owner_name_under_two_users_are_two_holders(http):
    assert http.post(f"{BASE}/7", json={"owner": "s1"}, headers=as_user("alice")).status_code == 200
    assert http.post(f"{BASE}/8", json={"owner": "s1"}, headers=as_user("bob")).status_code == 200
    claims = http.get(BASE, headers=as_user("alice")).json()["claims"]
    assert sorted(c["agent_id"] for c in claims) == ["alice/s1", "bob/s1"]


def test_owner_cannot_smuggle_another_principal_prefix(http):
    r = http.post(f"{BASE}/7", json={"owner": "alice/s1"}, headers=as_user("bob"))
    assert r.status_code == 400


def test_who_and_list_without_organization_header(http):
    assert http.get(f"{BASE}/7", headers=as_user("alice")).json() == {"held": False}
    http.post(f"{BASE}/7", json={"owner": "s1"}, headers=as_user("alice"))
    assert http.get(f"{BASE}/7", headers=as_user("alice")).json()["holder"]["agent_id"] == "alice/s1"
    r = http.get(BASE, headers=as_user("alice"))
    assert r.status_code == 200 and len(r.json()["claims"]) == 1


def test_anonymous_is_401_everywhere(http):
    assert http.post(f"{BASE}/7", json={"owner": "s1"}).status_code == 401
    assert http.delete(f"{BASE}/7", params={"owner": "s1"}).status_code == 401
    assert http.get(f"{BASE}/7").status_code == 401
    assert http.get(BASE).status_code == 401


def test_service_principal_reads_but_cannot_write(http):
    svc = as_user("service:slm", **{"X-Test-Service": "1"})
    assert http.get(BASE, headers=svc).status_code == 200
    assert http.post(f"{BASE}/7", json={"owner": "s1"}, headers=svc).status_code == 403
    assert http.delete(f"{BASE}/7", params={"owner": "s1"}, headers=svc).status_code == 403


@pytest.mark.parametrize(
    "number,body",
    [(0, {"owner": "s1"}), (-3, {"owner": "s1"}), (7, {"owner": "  "}), (7, {"owner": "s1", "ttl_s": 5})],
)
def test_bad_number_owner_or_ttl_is_rejected_before_the_registry(http, number, body):
    r = http.post(f"{BASE}/{number}", json=body, headers=as_user("alice"))
    assert r.status_code in (400, 422)
    assert http.get(BASE, headers=as_user("alice")).json()["claims"] == []


def test_unreadable_registry_fails_closed(http, monkeypatch):
    async def _none(database: str = "main"):
        return None

    monkeypatch.setattr(work_claims, "get_async_redis_client", _none)
    r = http.post(f"{BASE}/7", json={"owner": "s1"}, headers=as_user("alice"))
    assert r.status_code == 503
