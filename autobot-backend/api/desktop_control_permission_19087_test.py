# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Desktop control is gated on `mcp.desktop.control`, not the admin role (#19087).

THREAT_MODEL section 5 names `enforce_ws_desktop_auth` the one gate. `/ws/desktop`
and the vnc_mcp REST control tools checked `is_admin_role` instead. `superadmin`
is an admin role that holds no granular permissions (ROLE_PERMISSIONS), so it is
the real-world "admin without mcp.desktop.control"; `admin` holds the permission.
Only the credential seams are patched; the gates are the real code.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth_middleware
import auth_rbac
from api import advanced_control, vnc_mcp, ws_security
from autobot_shared.auth.permissions import Permission, is_admin_role, role_has_permission

CONTROL_PATHS = [
    "/mcp/desktop_mouse_click",
    "/mcp/desktop_keyboard_type",
    "/mcp/desktop_special_key",
    "/mcp/desktop_screenshot",
    "/mcp/desktop_observe_state",
]


def _user(role: str) -> dict:
    return {"username": f"{role}-u", "user_id": f"id-{role}", "role": role}


def test_premise_superadmin_is_admin_but_lacks_the_permission() -> None:
    assert is_admin_role("superadmin")
    assert not role_has_permission("superadmin", Permission.MCP_DESKTOP_CONTROL)
    assert role_has_permission("admin", Permission.MCP_DESKTOP_CONTROL)


def _rest_client(role: str) -> TestClient:
    app = FastAPI()
    app.include_router(vnc_mcp.router)
    return TestClient(app, raise_server_exceptions=False)


def _rest_patches(role: str):
    user = _user(role)
    return (
        patch.object(auth_middleware, "resolve_human_user", AsyncMock(return_value=user)),
        patch.object(auth_rbac, "resolve_human_user", AsyncMock(return_value=user)),
        patch.object(auth_rbac._permission_gate, "_wire_rbac", lambda: None),
        patch.object(auth_rbac._permission_gate, "_load_db_names", AsyncMock(return_value=())),
        patch.object(auth_rbac._permission_gate, "deny", AsyncMock(side_effect=_raise_403)),
    )


def _raise_403(*_a, **_k):
    from fastapi import HTTPException

    raise HTTPException(status_code=403, detail="denied")


def _post(role: str, path: str) -> int:
    p = _rest_patches(role)
    with p[0], p[1], p[2], p[3], p[4]:
        return _rest_client(role).post(path, json={}).status_code


@pytest.mark.parametrize("path", CONTROL_PATHS)
def test_rest_admin_without_permission_is_refused(path: str) -> None:
    assert _post("superadmin", path) == 403


@pytest.mark.parametrize("path", CONTROL_PATHS)
def test_rest_permission_holder_passes_the_gate(path: str) -> None:
    # The gate admits; the handler may then fail validation/capture, but never 401/403.
    assert _post("admin", path) not in (401, 403)


class _FakeWS:
    def __init__(self) -> None:
        self.accepted = False
        self.closed_with: tuple[int, str] | None = None

    async def accept(self, *a, **k) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


async def _accept(w) -> None:
    await w.accept()


async def _ws_connect(role: str) -> _FakeWS:
    """Run the real endpoint with only the credential seams patched."""
    ws = _FakeWS()
    streaming = MagicMock()
    streaming.handle_websocket_client = AsyncMock()
    with (
        patch.object(advanced_control, "enforce_ws_origin", AsyncMock(return_value=True)),
        patch.object(ws_security, "_resolve_ws_device_credential", AsyncMock(return_value=None)),
        patch.object(ws_security, "enforce_ws_authentication", AsyncMock(return_value=_user(role))),
        patch.object(advanced_control, "get_desktop_streaming", return_value=streaming),
        patch.object(advanced_control, "accept_websocket", _accept),
    ):
        await advanced_control.desktop_streaming_websocket(ws, "s1")
    ws.handled = streaming.handle_websocket_client.await_count
    return ws


@pytest.mark.asyncio
async def test_ws_admin_without_permission_is_refused() -> None:
    ws = await _ws_connect("superadmin")
    assert ws.closed_with is not None and ws.closed_with[0] == 1008
    assert not ws.accepted and ws.handled == 0


@pytest.mark.asyncio
async def test_ws_permission_holder_is_admitted() -> None:
    ws = await _ws_connect("admin")
    assert ws.closed_with is None, ws.closed_with
    assert ws.accepted and ws.handled == 1
