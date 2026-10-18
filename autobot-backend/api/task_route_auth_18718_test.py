# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Guard: task ``status/{task_id}`` and ``clear-stuck`` routes carry an auth gate (#18718).

Walks the real routers' ``.routes`` (``dependant.dependencies`` already merges
router-level and route-level dependencies). Explicit module list, no tree scan.
"""

import importlib
import re

import pytest
from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute

from auth_middleware import check_admin_permission, get_current_user

ROUTER_MODULES = [
    "api.analytics",
    "api.analytics_bug_prediction",
    "api.code_intelligence",
    "api.codebase_analytics.endpoints.dependencies",
    "api.codebase_analytics.endpoints.duplicates",
    "api.codebase_analytics.endpoints.import_tree",
    "api.codebase_analytics.endpoints.indexing",
    "api.codebase_analytics.endpoints.pattern_analysis",
]
_TASK_PATH = re.compile(r"/status/\{task_id\}$|/clear-stuck$")
_GATES = {get_current_user, check_admin_permission}


def _task_routes(router):
    return [r for r in router.routes if isinstance(r, APIRoute) and _TASK_PATH.search(r.path)]


def _is_gated(route) -> bool:
    return any(d.call in _GATES for d in route.dependant.dependencies)


def ungated_task_routes(router) -> list:
    """Return ``METHOD path`` of every status/clear-stuck route lacking a gate."""
    return [f"{sorted(r.methods)[0]} {r.path}" for r in _task_routes(router) if not _is_gated(r)]


def _ungated_paths(module):
    router = importlib.import_module(module).router
    assert _task_routes(router), f"{module}: no status/clear-stuck routes found (matcher drift?)"
    return {r.path for r in _task_routes(router) if not _is_gated(r)}


@pytest.mark.parametrize("module", ROUTER_MODULES)
def test_task_routes_are_gated(module):
    assert _ungated_paths(module) == set()


def test_clear_stuck_gates_are_admin():
    for module in ROUTER_MODULES:
        for r in _task_routes(importlib.import_module(module).router):
            calls = {d.call for d in r.dependant.dependencies}
            if r.path.endswith("/clear-stuck"):
                assert check_admin_permission in calls, f"{module} {r.path}"


def test_planted_control_ungated_route_is_flagged():
    fake = APIRouter()

    @fake.get("/x/status/{task_id}")
    async def _bare(task_id: str):
        return {}

    @fake.post("/x/tasks/clear-stuck", dependencies=[Depends(get_current_user)])
    async def _gated():
        return {}

    assert ungated_task_routes(fake) == ["GET /x/status/{task_id}"]


# --- behaviour ---
# conftest replaces ``auth_middleware`` with a stub, so the gates cannot be exercised
# through the real middleware here. Each caller class is modelled by overriding the
# very callables the routes captured in ``Depends`` -- a route lacking the gate
# ignores the override and runs its handler, which is what these tests detect.

PATTERN = "api.codebase_analytics.endpoints.pattern_analysis"
BUG = "api.analytics_bug_prediction"


def _deny(code):
    from fastapi import HTTPException

    def _dep():
        raise HTTPException(status_code=code)

    return _dep


def _allow(*_a, **_k):
    return {"username": "a", "role": "admin"}


def _client(module, caller):
    """TestClient over ``module``'s router; ``caller`` is anonymous, user or admin."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    mod = importlib.import_module(module)
    app.include_router(mod.router)
    if caller == "anonymous":
        app.dependency_overrides[get_current_user] = _deny(401)
        app.dependency_overrides[check_admin_permission] = _deny(401)
    elif caller == "user":
        app.dependency_overrides[get_current_user] = _allow
        app.dependency_overrides[check_admin_permission] = _deny(403)
    else:
        app.dependency_overrides[get_current_user] = _allow
        app.dependency_overrides[check_admin_permission] = lambda: True
    return TestClient(app)


@pytest.mark.parametrize("caller,code", [("anonymous", 401), ("user", 403)])
def test_pattern_task_revoke_requires_admin_and_does_not_revoke(caller, code):
    from unittest.mock import patch

    client = _client(PATTERN, caller)
    with patch("celery_app.celery_app.control.revoke") as revoke:
        assert client.delete("/patterns/task/x").status_code == code
    revoke.assert_not_called()


def test_pattern_task_revoke_admin_revokes():
    from unittest.mock import patch

    client = _client(PATTERN, "admin")
    with patch("celery_app.celery_app.control.revoke") as revoke:
        assert client.delete("/patterns/task/x").status_code == 200
    revoke.assert_called_once_with("x", terminate=True)


@pytest.mark.parametrize("path", ["/patterns/result/x", "/patterns/tasks"])
def test_pattern_result_and_list_require_authentication(path):
    assert _client(PATTERN, "anonymous").get(path).status_code == 401


@pytest.mark.parametrize("caller,code", [("anonymous", 401), ("user", 403), ("admin", 200)])
def test_bug_prediction_clear_stuck_gated_and_same_body(caller, code):
    resp = _client(BUG, caller).post("/bug-prediction/tasks/clear-stuck?force=true")
    assert resp.status_code == code
    if code == 200:
        assert resp.json() == {"cleared_count": 0, "message": "Celery handles task recovery automatically"}


def test_every_clear_stuck_route_refuses_the_service_principal():
    """Destructive task routes are human-admin-only (owner, 2026-10-18; #18688)."""
    from security.human_admin import refuse_service_principal

    for module in ROUTER_MODULES:
        for r in _task_routes(importlib.import_module(module).router):
            if r.path.endswith("/clear-stuck"):
                assert refuse_service_principal in {d.call for d in r.dependant.dependencies}, (module, r.path)


@pytest.fixture
def internal_key(monkeypatch):
    import security.human_admin as human_admin

    monkeypatch.setattr(human_admin.auth_middleware, "verify_internal_api_key", lambda key: key == "svc-key")
    return {"X-Internal-API-Key": "svc-key"}


def test_the_internal_key_cannot_revoke_a_pattern_task(internal_key):
    from unittest.mock import patch

    client = _client(PATTERN, "admin")  # the key's principal is admin; the refusal must still win
    with patch("celery_app.celery_app.control.revoke") as revoke:
        assert client.delete("/patterns/task/x", headers=internal_key).status_code == 403
    revoke.assert_not_called()


def test_the_internal_key_cannot_clear_stuck_tasks(internal_key):
    resp = _client(BUG, "admin").post("/bug-prediction/tasks/clear-stuck?force=true", headers=internal_key)
    assert resp.status_code == 403
