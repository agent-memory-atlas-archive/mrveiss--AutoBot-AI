# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""
Per-run provider credential injection (GH#9037).

Lightweight, dependency-free primitives so they can be imported (and unit
tested) without pulling in the full provider stack in ``provider_registry``.
Credentials are scoped to the current async task via a ``ContextVar``, never
persisted, and redacted from logs.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Dict, Optional

# Context variable for per-run credential overrides (GH#9037). Per async task,
# so concurrent requests never see each other's credentials.
_run_credentials: ContextVar[Optional["RunCredentialContext"]] = ContextVar("run_credentials", default=None)


class RunCredentialContext:
    """Per-run provider credentials for multi-tenant deployments (GH#9037).

    Allows each workflow or task invocation to inject provider API keys at
    runtime, scoped to the single request. Credentials never persist beyond
    the invocation and are redacted from all logs.

    Attributes:
        provider_credentials: Dict mapping provider names to their settings.
                              Example: {"anthropic": {"api_key": "sk-..."}}
    """

    def __init__(self, provider_credentials: Dict[str, Dict[str, Any]] | None = None) -> None:
        """Initialize the credential context.

        Args:
            provider_credentials: Mapping of provider name → settings dict
                                  (api_key, base_url, etc).
        """
        self.provider_credentials = provider_credentials or {}

    def get_credentials(self, provider_name: str) -> Dict[str, Any] | None:
        """Return runtime credentials for a provider, or None if not provided."""
        return self.provider_credentials.get(provider_name)

    def __repr__(self) -> str:
        """Redacted representation — never logs actual credential values."""
        providers = list(self.provider_credentials.keys())
        return f"<RunCredentialContext providers={providers}>"


def set_run_credentials(context: RunCredentialContext | None) -> None:
    """Set the per-run credential context for the current async task (GH#9037).

    Call at the start of a request handler to inject per-run credentials. The
    context is scoped to the current async task and will not leak to concurrent
    requests. Pass None to clear.
    """
    _run_credentials.set(context)


def get_run_credentials() -> RunCredentialContext | None:
    """Return the active RunCredentialContext for this async task, or None."""
    return _run_credentials.get()


# The principal an LLM call is made for (#19241): ``(user_id, org_id)``. Same task
# scoping as the run credentials above, so concurrent requests never see each
# other's principal. Read by ``llm_shared.provider_oauth_credentials`` to pick a
# signed-in provider token; None means "no user" (system token, then API key).
_llm_principal: ContextVar[Optional[tuple]] = ContextVar("llm_principal", default=None)


def set_llm_principal(user_id: Any, org_id: Any = None) -> None:
    """Record who the current task's LLM calls are for; empty ids clear it."""
    uid, org = str(user_id or "").strip(), str(org_id or "").strip()
    _llm_principal.set((uid, org) if uid or org else None)


def get_llm_principal() -> tuple | None:
    """Return ``(user_id, org_id)`` for the current task, or None when no principal is set."""
    return _llm_principal.get()


__all__ = [
    "RunCredentialContext",
    "set_run_credentials",
    "get_run_credentials",
    "set_llm_principal",
    "get_llm_principal",
]
