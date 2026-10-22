# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""
Provider auth connect/disconnect endpoints (#10551, #19241).

Provides backend API surface for the "Sign in with {provider}" flow and
device-code grant. Provider endpoints, scopes and the OAuth client come from
the server-side registry (``llm_shared.llm_oauth_registry``) — never from the
client. The OAuth HTTP exchange is delegated to
``knowledge.connectors.oauth_flow``; token storage uses the unified secrets
vault (``services.envelope_secrets_service``).

Every flow runs at a scope (#19241): ``system`` (the org-shared credential,
admin only) or ``user`` (the caller's own provider account).

Routes
------
GET  /api/llm-auth/providers               — sign-in providers + whether each is configured
POST /api/llm-auth/oauth/initiate          — mint PKCE+state, return provider authorize URL
POST /api/llm-auth/oauth/callback          — state-bound single-use code exchange + persist tokens
POST /api/llm-auth/device/initiate         — begin device-code flow
POST /api/llm-auth/device/poll             — poll for device-code approval + persist
GET  /api/llm-auth/status/{provider_name}  — check whether a provider has a stored token
DELETE /api/llm-auth/{provider_name}       — revoke / delete stored tokens
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api import provider_auth_flow_state as flow_state
from api.provider_auth_poll_limit import device_poll_key, load_and_check_poll_state, record_poll
from api.provider_auth_scope import (
    caller_is_admin,
    current_user_id,
    resolve_scope,
    subject_for,
    validate_redirect_uri,
)
from api.schemas_llm_auth import (
    AuthScope,
    DeviceInitiateRequest,
    DeviceInitiateResponse,
    DevicePollRequest,
    OAuthAuthorizeResponse,
    OAuthCallbackRequest,
    OAuthInitiateRequest,
    OAuthInitiateResponse,
    ProviderAuthStatus,
    ProviderListing,
)
from api.user_management.dependencies import get_db_session
from auth_middleware import get_current_user
from autobot_shared.http_egress_guard import assert_public_egress
from autobot_shared.logging_manager import get_logger
from autobot_shared.redis_client import get_redis_client
from autobot_shared.secret_redaction import redact_text
from autobot_shared.url_safety import get_oauth_allowed_hosts, require_allowlisted_https
from llm_shared.llm_oauth_registry import (
    FLOW_DEVICE,
    FLOW_REDIRECT,
    LlmOAuthClient,
    ProviderNotConfiguredError,
    UnknownProviderError,
    describe_providers,
    resolve_client,
)
from llm_shared.provider_auth import _vault_read_dual, _vault_write, build_token_data, validate_token_response

logger = get_logger(__name__)

# Kept as module names: callers and tests address the state key / principal id here (#11297, #11849).
_state_key = flow_state.state_key
_current_user_id = current_user_id

# ---------------------------------------------------------------------------
# SSRF allowlist — hosts that provider OAuth/token endpoints may live on.
# The list lives in autobot_shared.url_safety (single source of truth).
# Override at runtime via AUTOBOT_PROVIDER_OAUTH_ALLOWED_HOSTS (comma-separated).
# ---------------------------------------------------------------------------


def _validate_outbound_url(url: str) -> None:
    """Reject URLs that could cause SSRF (any violation → HTTP 400).

    Delegates the https / IP-literal / allowlist / port-pinning rules to the shared
    ``require_allowlisted_https`` guard in ``autobot_shared.url_safety`` so there is
    a single SSRF module (#11091 item 3), translating its ``ValueError`` into an
    HTTP 400. The allowlist is evaluated at call time via ``get_oauth_allowed_hosts()``
    so AUTOBOT_PROVIDER_OAUTH_ALLOWED_HOSTS env-var overrides are always respected.
    """
    try:
        require_allowlisted_https(url, get_oauth_allowed_hosts())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _reject_redirect(resp, what: str) -> None:
    """Turn an un-followed 3xx into a clean 502 (#13311).

    ``allow_redirects=False`` stops a compromised-but-allowlisted provider
    redirecting us to an internal host; a 3xx is never a valid answer here.
    """
    if not 300 <= resp.status < 400:
        return
    location = resp.headers.get("Location", "")
    logger.warning("%s returned HTTP %s; redirects are disabled (SSRF guard)", what, resp.status)
    target = f" -> {location[:100]}" if location else ""
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"{what} redirected (HTTP {resp.status}){target}; redirects are disabled to prevent SSRF",
    )


async def _pinned_connector(url: str):
    """Resolve *url* once, assert it is public, and return an IP-pinned connector.

    Finding #2 (#11497): the outbound token/device POST connects to the
    pre-resolved public IP so DNS cannot be rebound to a private address between
    the allowlist check and the socket connect. Translates an SSRF failure into
    HTTP 400. Callers must pass the returned connector to their ClientSession /
    ``exchange_code`` so the session owns and closes it.
    """
    from autobot_shared.security.ssrf_guard import SSRFError, pinned_connector  # noqa: PLC0415

    try:
        return await pinned_connector(url)
    except SSRFError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# The registry/app-factory prepends "/api" (see feature_routers.py), so this
# router carries only "/llm-auth" → resolves to /api/llm-auth (#11092).
router = APIRouter(prefix="/llm-auth", tags=["llm-auth"])

# System vault string — provider-level tokens live in the system vault. Within it,
# each token's SUBJECT is keyed to the org (system scope, #11497) or to the user
# (user scope, #19241), so connections never cross orgs or users.
_SYSTEM_VAULT = "system"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_client(provider_name: str, flow: str) -> LlmOAuthClient:
    """Registry lookup → 404 unknown / 400 unconfigured / 503 unreadable secret; SSRF-checks its URLs."""
    from services.provider_key_vault import VaultKeyUnavailableError  # noqa: PLC0415  # heavy import

    try:
        client = resolve_client(provider_name, flow)
    except UnknownProviderError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.args[0]) from exc
    except ProviderNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except VaultKeyUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    provider = client.provider
    for url in (provider.authorize_url, provider.token_url, provider.device_authorization_url):
        if url:
            _validate_outbound_url(url)
    return client


def _main_redis():
    redis = get_redis_client(database="main")
    flow_state.require_redis(redis)
    return redis


def _client_auth(client: LlmOAuthClient) -> dict:
    """The client-identifying form fields for a device-flow POST."""
    fields = {"client_id": client.client_id}
    if client.client_secret:
        fields["client_secret"] = client.client_secret
    return fields


async def _post_provider(url: str, payload: dict, what: str, *, reject_errors: bool) -> dict:
    """POST *payload* to a registry endpoint (SSRF-pinned, no redirects); return the JSON body."""
    import aiohttp  # noqa: PLC0415

    try:
        await assert_public_egress(url)  # public-only (#17576)
        connector = await _pinned_connector(url)  # finding #2: resolve-once + IP pin
        timeout = aiohttp.ClientTimeout(total=30.0)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as http:
            # SSRF: url passed _validate_outbound_url() in _resolve_client + assert_public_egress();
            # the connector pins the pre-resolved public IP; redirects disabled. (#12278, #17576)
            async with http.post(url, data=payload, headers={"Accept": "application/json"}, allow_redirects=False) as r:
                await _reject_redirect(r, what)
                if reject_errors and r.status >= 400:
                    body = redact_text((await r.text())[:200])
                    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"{what} failed: {body}")
                return await r.json(content_type=None)
    except aiohttp.ClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=redact_text(str(exc))) from exc


async def _persist(session, user: Any, ctx: dict, resp: dict, client_id: str) -> OAuthInitiateResponse:
    """Validate a token response and store it under the flow's own scope/subject."""
    provider_name = ctx["provider_name"]
    try:  # finding #3: reject a mismatched token_type / issuer / audience before persist
        validate_token_response(resp, provider_name=provider_name, client_id=client_id)
    except Exception as exc:
        logger.warning("Token validation failed for provider %s", provider_name)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    token_data = build_token_data(resp, created_by=_current_user_id(user))
    await _vault_write(
        session,
        provider_name=provider_name,
        subject=ctx["subject"],
        owner_vault_str=_SYSTEM_VAULT,
        token_data=token_data,
        created_by_id=_current_user_id(user),
    )
    await session.commit()
    logger.info("Provider token stored for %s (scope=%s)", provider_name, ctx["scope"])
    return OAuthInitiateResponse(
        provider_name=provider_name, stored=True, expires_at=token_data.get("expires_at"), scope=ctx["scope"]
    )


def _flow_context(user: Any, provider_name: str, scope: str) -> dict:
    """What a completing request is bound to: initiator, provider, scope and vault subject."""
    return {
        "initiator_id": _current_user_id(user),
        "provider_name": provider_name,
        "scope": scope,
        "subject": subject_for(scope, user),
    }


def _require_completion_allowed(ctx: dict, user: Any, is_admin: bool) -> None:
    """The completing caller must be the initiator; a system flow also still needs admin."""
    flow_state.require_initiator(ctx, _current_user_id(user))
    resolve_scope(ctx.get("scope"), is_admin)


# ---------------------------------------------------------------------------
# Provider listing
# ---------------------------------------------------------------------------


@router.get("/providers", response_model=List[ProviderListing])
async def list_providers(user: Any = Depends(get_current_user)) -> List[ProviderListing]:
    """List the sign-in providers, their configured flows and missing setting NAMES — never a value."""
    return [ProviderListing(**row) for row in describe_providers()]


# ---------------------------------------------------------------------------
# OAuth authorization-code flow (server-side code exchange)
# ---------------------------------------------------------------------------


@router.post("/oauth/initiate", response_model=OAuthAuthorizeResponse)
async def oauth_initiate(
    req: OAuthInitiateRequest,
    user: Any = Depends(get_current_user),
    is_admin: bool = Depends(caller_is_admin),
) -> OAuthAuthorizeResponse:
    """Begin a provider auth-code + PKCE flow; return the authorize URL + state.

    The provider and client come from the registry; ``redirect_uri`` must be on
    the SSOT frontend origin. The server mints the PKCE verifier and a single-use
    ``state`` bound to the caller, scope and subject (#11297, #19241).
    """
    from knowledge.connectors.oauth_flow import build_authorize_url, generate_pkce, generate_state  # noqa: PLC0415

    ctx = _flow_context(user, req.provider_name, resolve_scope(req.scope, is_admin))
    client = _resolve_client(req.provider_name, FLOW_REDIRECT)
    validate_redirect_uri(req.redirect_uri)
    redis = _main_redis()
    verifier, challenge = generate_pkce()
    state = generate_state()
    payload = {**ctx, "verifier": verifier, "redirect_uri": req.redirect_uri}
    await flow_state.save(redis, _state_key(state), flow_state.OAUTH_STATE_TTL_SECONDS, payload)
    authorize_url = build_authorize_url(client.provider, client.client_id, req.redirect_uri, state, challenge)
    logger.info("OAuth authorize initiated for provider %s (scope=%s)", req.provider_name, ctx["scope"])
    return OAuthAuthorizeResponse(authorize_url=authorize_url, state=state)


async def _exchange(client: LlmOAuthClient, code: str, redirect_uri: str, verifier: str) -> dict:
    """Exchange *code* at the registry token endpoint (SSRF-pinned; secret sent only if configured)."""
    from knowledge.connectors.oauth_flow import exchange_code  # noqa: PLC0415

    connector = await _pinned_connector(client.provider.token_url)  # finding #2: resolve-once + IP pin
    try:
        # SSRF mitigated: token_url passed _validate_outbound_url() in _resolve_client and
        # _pinned_connector() pins the public IP; redirects disabled in exchange_code. (#12278)
        return await exchange_code(
            client.provider, client.client_id, client.client_secret, code, redirect_uri, verifier, connector=connector
        )
    except RuntimeError as exc:
        # Log provider name only — never log code or token values.
        logger.warning("OAuth code exchange failed for provider %s", client.provider.name)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=redact_text(str(exc))) from exc


@router.post("/oauth/callback", response_model=OAuthInitiateResponse)
async def oauth_callback(
    req: OAuthCallbackRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user),
    is_admin: bool = Depends(caller_is_admin),
) -> OAuthInitiateResponse:
    """Exchange an OAuth authorization code for tokens and persist to the vault.

    Authorized by a server-minted, **single-use** ``state``: atomically consumed
    (GETDEL), it must belong to the caller and supplies the verifier, provider,
    scope and subject — none of which the client provides (#11297). The token is
    written to the scope the flow started with (#19241).
    """
    redis = _main_redis()
    stored = await flow_state.consume_oauth_state(redis, req.state)
    _require_completion_allowed(stored, user, is_admin)
    if req.redirect_uri != stored.get("redirect_uri"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="redirect_uri does not match the flow")
    client = _resolve_client(stored["provider_name"], FLOW_REDIRECT)
    resp = await _exchange(client, req.code, req.redirect_uri, stored["verifier"])
    return await _persist(session, user, stored, resp, client.client_id)


# ---------------------------------------------------------------------------
# Device-code flow
# ---------------------------------------------------------------------------


@router.post("/device/initiate", response_model=DeviceInitiateResponse)
async def device_initiate(
    req: DeviceInitiateRequest,
    user: Any = Depends(get_current_user),
    is_admin: bool = Depends(caller_is_admin),
) -> DeviceInitiateResponse:
    """Start a device-code flow at the registry's device endpoint (RFC 8628).

    Returns the ``user_code`` + ``verification_uri``; the context (caller, scope,
    subject) is held server-side so /device/poll stores to the right place.
    """
    ctx = _flow_context(user, req.provider_name, resolve_scope(req.scope, is_admin))
    client = _resolve_client(req.provider_name, FLOW_DEVICE)
    redis = _main_redis()
    payload = {**_client_auth(client), "scope": " ".join(client.provider.default_scopes)}
    url = client.provider.device_authorization_url
    data = await _post_provider(url, payload, "Device authorization endpoint", reject_errors=True)
    if not data.get("device_code") or not data.get("user_code"):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="device authorization returned no code")
    resp = DeviceInitiateResponse(
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data.get("verification_uri") or data.get("verification_url", ""),
        expires_in=int(data.get("expires_in", 1800)),
        interval=int(data.get("interval", 5)),
    )
    await flow_state.save(redis, flow_state.device_context_key(resp.device_code), resp.expires_in, ctx)
    return resp


@router.post("/device/poll", response_model=OAuthInitiateResponse)
async def device_poll(
    req: DevicePollRequest,
    session: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user),
    is_admin: bool = Depends(caller_is_admin),
) -> OAuthInitiateResponse:
    """Poll the token endpoint for a device-code grant and persist on approval.

    Returns ``stored=False`` while the grant is pending. Only the initiator may
    poll, and the token goes to the scope the flow started with (#19241).
    """
    redis = _main_redis()
    ctx = await flow_state.load_device_context(redis, req.device_code, req.provider_name)
    _require_completion_allowed(ctx, user, is_admin)
    client = _resolve_client(req.provider_name, FLOW_DEVICE)
    # RFC-8628 server-side poll limiter (#11061): reject too-fast polls and cap
    # total attempts BEFORE hitting the provider.
    poll_key, now = device_poll_key(req.device_code), time.time()
    poll_state = await load_and_check_poll_state(redis, poll_key, now)
    grant = {"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": req.device_code}
    # A 400 is normal here (RFC 8628 carries `authorization_pending` in it).
    data = await _post_provider(
        client.provider.token_url, {**grant, **_client_auth(client)}, "Token endpoint", reject_errors=False
    )
    error = data.get("error")
    await record_poll(redis, poll_key, poll_state, now, error)
    if error in ("authorization_pending", "slow_down"):
        return OAuthInitiateResponse(provider_name=req.provider_name, stored=False, scope=ctx["scope"])
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=data.get("error_description", error))
    result = await _persist(session, user, ctx, data, client.client_id)
    await flow_state.drop_device_context(redis, req.device_code)
    return result


# ---------------------------------------------------------------------------
# Status + revoke
# ---------------------------------------------------------------------------


@router.get("/status/{provider_name}", response_model=ProviderAuthStatus)
async def provider_auth_status(
    provider_name: str,
    scope: Optional[AuthScope] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user),
    is_admin: bool = Depends(caller_is_admin),
) -> ProviderAuthStatus:
    """Return the caller's connection status for a provider at *scope*.

    System scope dual-reads (finding #1, #11497): the org-scoped token, else the
    legacy ``"global"`` one. User scope reads only the caller's own token.
    """
    effective = resolve_scope(scope, is_admin)
    token_data = await _vault_read_dual(
        session, provider_name=provider_name, subject=subject_for(effective, user), owner_vault_str=_SYSTEM_VAULT
    )
    if token_data is None:
        return ProviderAuthStatus(provider_name=provider_name, connected=False, scope=effective)
    expires_at = token_data.get("expires_at")
    return ProviderAuthStatus(
        provider_name=provider_name,
        connected=expires_at is None or time.time() < expires_at,
        expires_at=expires_at,
        auth_kind=token_data.get("auth_kind", "oauth"),
        scope=effective,
    )


@router.delete("/{provider_name}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_provider_auth(
    provider_name: str,
    scope: Optional[AuthScope] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    user: Any = Depends(get_current_user),
    is_admin: bool = Depends(caller_is_admin),
) -> None:
    """Revoke the caller's stored token for a provider at *scope* (system needs admin).

    System scope deletes only the caller's org subject, keeping other orgs' legacy
    credential intact (#11497); user scope deletes only the caller's own (#19241).
    """
    from sqlalchemy import delete  # noqa: PLC0415

    from llm_shared.provider_auth import _vault_secret_name  # noqa: PLC0415
    from models.secret import Secret  # noqa: PLC0415

    effective = resolve_scope(scope, is_admin)
    name = _vault_secret_name(provider_name, subject_for(effective, user))
    await session.execute(delete(Secret).where(Secret.name == name, Secret.owner_vault == _SYSTEM_VAULT))
    await session.commit()
    logger.info("Revoked provider auth tokens for %s (scope=%s)", provider_name, effective)


__all__ = ["router"]
