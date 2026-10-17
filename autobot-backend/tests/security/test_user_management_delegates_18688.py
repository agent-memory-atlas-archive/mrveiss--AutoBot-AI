# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""user_management's ``get_current_user`` is the canonical gate (#18688).

Two things follow from delegating ~357 user_management-gated routes (mostly
``llc/api``) to ``auth_middleware.get_current_user``:

* a token minted before its subject's password changed is refused on those
  routes too (it was honoured there before), and
* the internal service key now resolves to a ``service`` principal, which is
  NOT a tenant admin (owner decision 2026-10-17): organization-scoped routes
  refuse it whatever ``X-Organization-Id`` it sends, while a non-tenant
  ``auth_middleware`` route still admits it.

The real canonical gate runs here; only storage (the epoch lookup) is stood in.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import api.user_management.dependencies as user_deps
import llc.api.approvals as llc_api
from llc.deps import get_session as llc_get_session
from testkit.canonical_gate import install_canonical_gate, no_user_middleware

INTERNAL_KEY = "internal-service-key-for-tests"
_JWT_USER = {
    "username": "alice",
    "role": "admin",
    "user_id": str(uuid.uuid4()),
    "iat": 1,
    "auth_method": "jwt",
}


def _llc_client() -> TestClient:
    app = FastAPI()
    app.include_router(llc_api.router, prefix="/api/llc")

    async def _session():
        yield AsyncMock()

    app.dependency_overrides[llc_get_session] = _session
    app.dependency_overrides[user_deps.get_db_session] = _session
    return TestClient(app, raise_server_exceptions=False)


def _jwt_middleware() -> MagicMock:
    middleware = no_user_middleware()
    middleware.get_user_from_request.return_value = dict(_JWT_USER)
    return middleware


@pytest.mark.parametrize("revoked, expected_401", [(True, True), (False, False)])
def test_a_password_change_revoked_jwt_is_refused_on_an_llc_route(monkeypatch, revoked, expected_401):
    import auth_revocation  # noqa: PLC0415

    install_canonical_gate(monkeypatch, _jwt_middleware())
    monkeypatch.setattr(auth_revocation, "is_token_revoked_by_password_change", AsyncMock(return_value=revoked))

    response = _llc_client().get("/api/llc/approvals", params={"company_id": str(uuid.uuid4())})

    assert (response.status_code == 401) is expected_401, response.text
    if expected_401:
        assert "password was changed" in response.json()["detail"]


def _service_key_gate(monkeypatch):
    real = install_canonical_gate(monkeypatch, no_user_middleware())
    monkeypatch.setattr(real, "verify_internal_api_key", lambda provided: provided == INTERNAL_KEY)
    return real


@pytest.mark.parametrize(
    "headers, params",
    [
        ({"X-Organization-Id": str(uuid.uuid4())}, {}),
        ({}, {"company_id": str(uuid.uuid4())}),
        ({"X-Organization-Id": str(uuid.uuid4())}, {"company_id": str(uuid.uuid4())}),
        ({}, {}),  # no org at all: still refused, not "org context required"
    ],
)
def test_the_internal_service_key_is_refused_on_an_org_scoped_route(monkeypatch, headers, params):
    _service_key_gate(monkeypatch)

    response = _llc_client().get(
        "/api/llc/approvals", params=params, headers={"X-Internal-API-Key": INTERNAL_KEY, **headers}
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == user_deps._SERVICE_PRINCIPAL_DENIED


def test_the_internal_service_key_still_works_on_a_non_tenant_route(monkeypatch):
    real = _service_key_gate(monkeypatch)
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(user: dict = Depends(real.get_current_user)):
        return user

    response = TestClient(app).get("/whoami", headers={"X-Internal-API-Key": INTERNAL_KEY})

    assert response.status_code == 200, response.text
    assert response.json() == {"username": "service:slm", "role": "admin", "service": True}


def test_a_service_principal_is_not_a_platform_admin_or_reporting_line_writer():
    service = {"username": "service:slm", "role": "admin", "service": True}

    assert user_deps._is_platform_admin_claim(service) is False
    assert user_deps._holds_reporting_line_write(service) is False
    # Control: the same role without the service marker keeps both grants.
    human = {"username": "alice", "role": "admin"}
    assert user_deps._is_platform_admin_claim(human) is True
    assert user_deps._holds_reporting_line_write(human) is True
