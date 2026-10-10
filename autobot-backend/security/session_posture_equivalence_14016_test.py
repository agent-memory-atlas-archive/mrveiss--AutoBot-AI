# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""One validator, two postures: each posture reproduces its old verdict, bar one row (#14016).

``OLD_STRICT`` is what the conversation-file routes' private validator returned; ``OLD_DEFAULT`` is
what the canonical dependency returned. Both columns were read from the code before it was merged
(issue decision table) and are frozen here. ``NEW_DEFAULT`` differs from ``OLD_DEFAULT`` in exactly
the unreadable-owner rows (#14033): they used to claim the session for the caller.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from security.enforcement_mode import ResolvedEnforcementMode
from security.session_owner_errors import SessionOwnerUnreadable
from security.session_ownership import validate_session_ownership
from security.session_posture import SessionPosture

ALLOW, DENY, ERROR, UNOWNED, UNREADABLE, BROKEN = "allow", "deny", "error", None, "unreadable", "broken"


def _case(
    name,
    *,
    role="user",
    user="alice",
    redis=UNOWNED,
    file=UNOWNED,
    mode="enforced",
    auth=True,
    shared=False,
    org=None,
    old_strict,
    old_default,
    new_default=None,
    ctx_org=None,
):
    return pytest.param(
        dict(
            role=role,
            user=user,
            redis=redis,
            file=file,
            mode=mode,
            auth=auth,
            shared=shared,
            org=org,
            ctx_org=ctx_org,
            old_strict=old_strict,
            old_default=old_default,
            new_default=new_default or old_default,
        ),
        id=name,
    )


CASES = [
    _case("owner matches", redis="alice", file="alice", old_strict=ALLOW, old_default=ALLOW),
    _case("stranger, enforced", redis="carol", file="carol", old_strict=DENY, old_default=DENY),
    _case("no role, is owner", role=None, redis="alice", file="alice", old_strict=DENY, old_default=ALLOW),
    _case("global admin, not owner", role="admin", redis="carol", file="carol", old_strict=ALLOW, old_default=DENY),
    _case(
        "org admin of the session's org",
        role="org_admin",
        org="o1",
        ctx_org="o1",
        redis="carol",
        file="carol",
        old_strict=DENY,
        old_default=ALLOW,
    ),
    _case("shared with the user", shared=True, redis="carol", file="carol", old_strict=DENY, old_default=ALLOW),
    _case("stranger, log_only", mode="log_only", redis="carol", file="carol", old_strict=DENY, old_default=ALLOW),
    _case(
        "stranger, enforcement disabled",
        mode="disabled",
        redis="carol",
        file="carol",
        old_strict=DENY,
        old_default=ALLOW,
    ),
    _case("stranger, auth disabled", auth=False, redis="carol", file="carol", old_strict=DENY, old_default=ALLOW),
    _case("expired cache, file names another", file="carol", old_strict=DENY, old_default=DENY),
    _case("unowned everywhere", old_strict=ALLOW, old_default=ALLOW),
    _case("owner unreadable, enforced", file=UNREADABLE, old_strict=DENY, old_default=ALLOW, new_default=DENY),
    _case(
        "owner unreadable, log_only",
        file=UNREADABLE,
        mode="log_only",
        old_strict=DENY,
        old_default=ALLOW,
        new_default=DENY,
    ),
    _case("owner lookup breaks (not the typed error)", file=BROKEN, old_strict=ERROR, old_default=ALLOW),
]


class _Redis:
    def __init__(self, owner, shared, ctx_org):
        self.owner, self.shared, self.ctx_org = owner, shared, ctx_org
        self.set = AsyncMock()
        self.sadd = AsyncMock()
        self.expire = AsyncMock()
        self.hset = AsyncMock()

    async def get(self, key):
        return self.owner

    async def sismember(self, key, member):
        return self.shared

    async def hgetall(self, key):
        return {"org_id": self.ctx_org} if self.ctx_org else {}


def _drive(monkeypatch, c):
    redis = _Redis(c["redis"], c["shared"], c["ctx_org"])
    user = {"username": c["user"], "user_id": c["user"], "role": c["role"], "org_id": c["org"]}
    middleware = SimpleNamespace(
        enable_auth=c["auth"], get_user_from_request=lambda r: user, security_layer=MagicMock()
    )
    manager = MagicMock()
    if c["file"] == UNREADABLE:
        manager.get_session_owner = AsyncMock(side_effect=SessionOwnerUnreadable("s"))
    elif c["file"] == BROKEN:
        manager.get_session_owner = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        manager.get_session_owner = AsyncMock(return_value=c["file"])
    for target in ("security.session_ownership", "security.session_posture"):
        monkeypatch.setattr(f"{target}.get_auth_middleware", lambda: middleware)
    monkeypatch.setattr("utils.chat_utils.get_chat_history_manager", lambda r: manager)
    monkeypatch.setattr(
        "security.session_ownership.resolve_enforcement_mode_for_request",
        AsyncMock(return_value=ResolvedEnforcementMode(c["mode"])),
    )
    monkeypatch.setattr("autobot_shared.redis_client.get_redis_client", AsyncMock(return_value=redis))
    monkeypatch.setattr("services.feature_flags.get_feature_flags", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr("services.access_control_metrics.get_metrics_service", AsyncMock(return_value=None))
    return redis


async def _verdict(posture, request=None):
    request = request or MagicMock()
    try:
        await validate_session_ownership("session-0001", request, posture=posture)
    except HTTPException as exc:
        return DENY if exc.status_code == 403 else f"http-{exc.status_code}"
    return ALLOW


@pytest.mark.asyncio
@pytest.mark.parametrize("c", CASES)
async def test_strict_posture_equals_the_old_file_route_verdict(monkeypatch, c) -> None:
    _drive(monkeypatch, c)
    request = MagicMock()
    verdict = await _verdict(SessionPosture.STRICT, request)
    expected = "http-500" if c["old_strict"] == ERROR else c["old_strict"]
    assert verdict == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("c", CASES)
async def test_default_posture_equals_the_old_canonical_verdict_except_the_unreadable_rows(monkeypatch, c) -> None:
    _drive(monkeypatch, c)
    assert await _verdict(SessionPosture.DEFAULT) == c["new_default"]


def test_the_only_intended_delta_is_the_unreadable_owner() -> None:
    changed = sorted(p.id for p in CASES if p.values[0]["old_default"] != p.values[0]["new_default"])
    assert changed == ["owner unreadable, enforced", "owner unreadable, log_only"]


@pytest.mark.asyncio
@pytest.mark.parametrize("posture", list(SessionPosture))
async def test_an_unreadable_owner_is_denied_and_the_session_is_not_claimed(monkeypatch, posture) -> None:
    case = dict(CASES[11].values[0])
    redis = _drive(monkeypatch, case)
    assert await _verdict(posture) == DENY
    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_contrast_a_genuinely_unowned_session_is_still_allowed_under_both(monkeypatch) -> None:
    redis = _drive(monkeypatch, dict(CASES[10].values[0]))
    assert await _verdict(SessionPosture.STRICT) == ALLOW
    redis.set.assert_not_awaited()  # strict never claims
    assert await _verdict(SessionPosture.DEFAULT) == ALLOW
    redis.set.assert_awaited()  # default claims it for the caller


def test_the_posture_is_not_a_client_controllable_parameter() -> None:
    from fastapi.dependencies.utils import get_dependant

    dependant = get_dependant(path="/chat/sessions/{session_id}", call=validate_session_ownership)
    assert [p.name for p in dependant.query_params] == []
    assert [p.name for p in dependant.path_params] == ["session_id"]
