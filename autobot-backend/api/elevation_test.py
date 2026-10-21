# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Elevation session tokens must never reach logs (#19082)."""

from unittest.mock import AsyncMock, patch

import pytest

from api import elevation
from api.schemas_workflows import ElevationAuthorization


def _logged_text(mock_logger) -> str:
    """Render every logger call (message % args) into one string."""
    out = []
    for call in mock_logger.method_calls:
        if call.args:
            msg, *args = call.args
            try:
                out.append(msg % tuple(args))
            except TypeError:
                out.append(f"{msg} {args}")
    return "\n".join(out)


@pytest.fixture(autouse=True)
def _clean_state():
    elevation.elevation_sessions.clear()
    elevation.pending_requests.clear()
    yield
    elevation.elevation_sessions.clear()
    elevation.pending_requests.clear()


async def _create_token() -> str:
    elevation.pending_requests["req-1"] = {"status": "pending"}
    auth = ElevationAuthorization(request_id="req-1", password="pw")
    with patch.object(elevation, "verify_sudo_password", AsyncMock(return_value=True)):
        result = await elevation.authorize_elevation(auth)
    return result["session_token"]


async def test_create_does_not_log_token():
    with patch.object(elevation, "logger") as mock_logger:
        token = await _create_token()
    text = _logged_text(mock_logger)
    assert "Elevation authorized" in text
    assert token not in text
    assert elevation._token_fingerprint(token) in text


async def test_use_does_not_log_token():
    token = await _create_token()
    run = AsyncMock(return_value={"stdout": "", "stderr": "", "return_code": 0})
    with patch.object(elevation, "logger") as mock_logger, patch.object(elevation, "run_elevated_command", run):
        await elevation.execute_elevated_command(token, "apt update")
    text = _logged_text(mock_logger)
    assert "Elevated command executed" in text
    assert token not in text


async def test_revoke_does_not_log_token():
    token = await _create_token()
    with patch.object(elevation, "logger") as mock_logger:
        await elevation.revoke_elevation_session(token)
    text = _logged_text(mock_logger)
    assert "Elevation session revoked" in text
    assert token not in text
    assert token not in elevation.elevation_sessions


def test_fingerprint_is_short_and_deterministic():
    fp = elevation._token_fingerprint("abc")
    assert len(fp) == 8 and fp == elevation._token_fingerprint("abc")
