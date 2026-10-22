# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Server-held flow state for ``/api/llm-auth`` sign-in (#11297, #19241).

Both flows bind their completion to the principal that started them, the
scope it chose and the vault subject that scope resolved to — none of which the
completing request may supply:

- redirect flow: a single-use ``state`` (GETDEL) minted by /oauth/initiate;
- device flow: a context keyed by a hash of the ``device_code``, written by
  /device/initiate and consumed when the token is stored.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException, status

from autobot_shared.env_utils import env_int
from autobot_shared.redis_client import client_get, client_getdel, client_setex

# OAuth authorize state is single-use and short-lived — a security window, not a
# cache (#11297). Overridable via env.
OAUTH_STATE_TTL_SECONDS = env_int("AUTOBOT_PROVIDER_OAUTH_STATE_TTL_SECONDS", 600)
_OAUTH_STATE_PREFIX = "llm-auth:oauth:state:"
_DEVICE_CTX_PREFIX = "llm-auth:device:ctx:"


def state_key(state: str) -> str:
    return "%s%s" % (_OAUTH_STATE_PREFIX, state)


def device_context_key(device_code: str) -> str:
    """Redis key for a device flow; the device_code itself is never a key (it is a bearer secret)."""
    return _DEVICE_CTX_PREFIX + hashlib.sha256(device_code.encode("utf-8")).hexdigest()


def _decode(raw) -> dict:
    return json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)


def require_redis(redis) -> None:
    if redis is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OAuth state store unavailable")


async def save(redis, key: str, ttl: int, payload: dict) -> None:
    await client_setex(redis, key, ttl, json.dumps(payload, ensure_ascii=False))


async def consume_oauth_state(redis, state: str) -> dict:
    """Atomically fetch-and-delete the state; missing/expired/replayed → 400."""
    raw = await client_getdel(redis, state_key(state))
    if raw is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired OAuth state")
    return _decode(raw)


async def load_device_context(redis, device_code: str, provider_name: str) -> dict:
    """The device flow's stored context; unknown/expired → 400, provider mismatch → 400."""
    raw = await client_get(redis, device_context_key(device_code))
    if raw is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown or expired device code")
    ctx = _decode(raw)
    if ctx.get("provider_name") != provider_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="device code is for another provider")
    return ctx


async def drop_device_context(redis, device_code: str) -> None:
    await client_getdel(redis, device_context_key(device_code))


def require_initiator(stored: dict, caller_id: str) -> None:
    """Bind the completing principal to the initiating one (#11297); 403 on mismatch."""
    if stored.get("initiator_id") != caller_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="sign-in flow does not belong to the current user"
        )
