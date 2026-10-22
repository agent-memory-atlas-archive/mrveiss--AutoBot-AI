# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Which signed-in provider token an LLM call uses (#19241).

Resolution order for a sign-in-capable provider (``llm_oauth_registry``):

1. the requesting user's own token (``user_vault_subject``);
2. the system token for the user's org (``org_vault_subject``, which dual-reads
   the legacy ``"global"`` subject, #11497);
3. ``None`` — the provider then uses its configured API key, as before.

The principal comes only from ``run_credentials.get_llm_principal()``, set by
trusted server code (chat turn, compat APIs). A call with no
principal (background jobs, schedulers, CLI) starts at step 2. An expired token
or a failed refresh falls through to the next source with a warning; a user
subject is only ever the requesting user's own, so no step can reach another
user's token. Refresh goes through ``OAuthAuth`` (the existing strategy).
"""

from __future__ import annotations

from typing import Any

from autobot_shared.logging_manager import get_logger

from .llm_oauth_registry import (
    LLM_OAUTH_PROVIDER_LABELS,
    build_provider,
    missing_settings,
    resolve_client,
)
from .provider_auth import (
    OAuthAuth,
    ProviderAuthError,
    _vault_read_dual,
    org_vault_subject,
    user_vault_subject,
)
from .run_credentials import get_llm_principal

logger = get_logger(__name__)

_SYSTEM_VAULT = "system"


def request_principal() -> tuple[str, str]:
    """``(user_id, org_id)`` for the current task, or empty strings when no principal is set.

    Only the server-set task principal counts -- never ``LLMRequest.metadata``,
    which some callers fill from client input, so a client cannot name a user.
    """
    return get_llm_principal() or ("", "")


def candidate_subjects(user_id: str, org_id: str) -> list[tuple[str, str]]:
    """``[(source, subject), ...]`` in resolution order: the user's own, then the org/system one."""
    subjects = [("user", user_vault_subject(user_id))] if user_id else []
    subjects.append(("system", org_vault_subject(org_id or None)))
    return subjects


def _refresh_strategy(provider_name: str, subject: str) -> OAuthAuth:
    """An ``OAuthAuth`` bound to *subject*, with the registry's client for refresh."""
    try:
        client = resolve_client(provider_name)
        token_url, client_id, secret = client.provider.token_url, client.client_id, client.client_secret
    except Exception as exc:  # unreadable vault secret: a still-valid token works; a refresh will fail and fall through
        logger.warning("sign-in client for %s unavailable for refresh (%s)", provider_name, type(exc).__name__)
        token_url, client_id, secret = build_provider(provider_name).token_url, "", ""
    return OAuthAuth(
        provider_name=provider_name,
        token_url=token_url,
        client_id=client_id,
        client_secret=secret,
        owner_vault_str=_SYSTEM_VAULT,
        subject=subject,
    )


async def _token_for_subject(session: Any, provider_name: str, source: str, subject: str) -> str | None:
    """A fresh access token stored under *subject*, or None (absent, expired, refresh failed)."""
    token_data = await _vault_read_dual(
        session, provider_name=provider_name, subject=subject, owner_vault_str=_SYSTEM_VAULT
    )
    if token_data is None:
        return None
    try:
        return await _refresh_strategy(provider_name, subject)._ensure_fresh(session, token_data)
    except ProviderAuthError as exc:
        logger.warning("%s %s sign-in token unusable (%s); trying the next source", provider_name, source, exc)
        return None


async def first_usable_token(session: Any, provider_name: str, user_id: str, org_id: str) -> str | None:
    """Walk :func:`candidate_subjects` and return the first usable token."""
    for source, subject in candidate_subjects(user_id, org_id):
        token = await _token_for_subject(session, provider_name, source, subject)
        if token:
            logger.debug("%s call uses the %s sign-in token", provider_name, source)
            return token
    return None


def sign_in_configured(provider_name: str) -> bool:
    """True when *provider_name* is a sign-in provider the operator has configured."""
    return provider_name in LLM_OAUTH_PROVIDER_LABELS and not missing_settings(build_provider(provider_name))


async def resolve_signed_in_token(provider_name: str) -> str | None:
    """The signed-in token for this call, or None to use the API key (see module docstring)."""
    if not sign_in_configured(provider_name):
        return None
    from user_management.database import db_session_context  # noqa: PLC0415  # heavy import

    user_id, org_id = request_principal()
    try:
        async with db_session_context() as session:
            return await first_usable_token(session, provider_name, user_id, org_id)
    except Exception as exc:  # store unreachable: the API key path still works, so say so and use it
        logger.warning("%s sign-in token lookup failed (%s); using the API key", provider_name, type(exc).__name__)
        return None


__all__ = [
    "candidate_subjects",
    "first_usable_token",
    "request_principal",
    "resolve_signed_in_token",
    "sign_in_configured",
]
