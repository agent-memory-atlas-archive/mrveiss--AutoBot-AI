# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Which signed-in token an LLM call uses (#19241).

Order: the requesting user's own token, then the org/system token, then None
(the provider's API key). A user never receives another user's token, and an
unusable token falls through to the next source.
"""

import time

import pytest

from llm_shared import provider_auth
from llm_shared import provider_oauth_credentials as creds
from llm_shared.run_credentials import get_llm_principal, set_llm_principal

_FRESH = time.time() + 3600


class _Vault:
    """Token store keyed by subject, standing in for the system vault."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.reads = []

    async def read(self, session, *, provider_name, subject, owner_vault_str):
        self.reads.append(subject)
        return self.tokens.get(subject)


@pytest.fixture
def vault(monkeypatch):
    def _install(tokens):
        fake = _Vault(tokens)
        monkeypatch.setattr(creds, "_vault_read_dual", fake.read)
        return fake

    return _install


def _token(value, expires_at=_FRESH, refresh=None):
    data = {"access_token": value, "expires_at": expires_at}
    if refresh:
        data["refresh_token"] = refresh
    return data


def test_candidate_order_is_user_then_system():
    assert creds.candidate_subjects("u-1", "org-9") == [("user", "user:u-1"), ("system", "org:org-9")]
    assert creds.candidate_subjects("", "") == [("system", "global")]


@pytest.mark.asyncio
async def test_user_token_wins_over_system(vault):
    vault({"user:u-1": _token("user-at"), "org:org-9": _token("system-at")})
    assert await creds.first_usable_token(None, "openai", "u-1", "org-9") == "user-at"


@pytest.mark.asyncio
async def test_system_token_when_user_has_none(vault):
    vault({"org:org-9": _token("system-at")})
    assert await creds.first_usable_token(None, "openai", "u-1", "org-9") == "system-at"


@pytest.mark.asyncio
async def test_none_when_no_token_so_the_api_key_is_used(vault):
    vault({})
    assert await creds.first_usable_token(None, "openai", "u-1", "org-9") is None


@pytest.mark.asyncio
async def test_user_b_never_gets_user_a_token(vault):
    fake = vault({"user:u-A": _token("a-at")})
    assert await creds.first_usable_token(None, "openai", "u-B", "") is None
    assert "user:u-A" not in fake.reads


@pytest.mark.asyncio
async def test_expired_user_token_falls_through_to_system(vault):
    vault({"user:u-1": _token("stale", expires_at=time.time() - 10), "global": _token("system-at")})
    assert await creds.first_usable_token(None, "openai", "u-1", "") == "system-at"


@pytest.mark.asyncio
async def test_user_subject_never_dual_reads_the_legacy_global(monkeypatch):
    reads = []

    async def _read(session, *, provider_name, subject, owner_vault_str):
        reads.append(subject)
        return {"access_token": "shared"} if subject == "global" else None

    monkeypatch.setattr(provider_auth, "_vault_read", _read)
    got = await provider_auth._vault_read_dual(
        None, provider_name="openai", subject="user:u-1", owner_vault_str="system"
    )
    assert got is None
    assert reads == ["user:u-1"]


@pytest.mark.asyncio
async def test_unconfigured_provider_skips_the_lookup(monkeypatch):
    monkeypatch.setattr(creds, "sign_in_configured", lambda name: False)

    async def _boom(*a, **k):
        raise AssertionError("no vault lookup for an unconfigured provider")

    monkeypatch.setattr(creds, "first_usable_token", _boom)
    assert await creds.resolve_signed_in_token("openai") is None


def test_principal_comes_from_the_task_context_only():
    set_llm_principal("u-1", "org-9")
    try:
        assert creds.request_principal() == ("u-1", "org-9")
    finally:
        set_llm_principal(None)
    assert get_llm_principal() is None
    assert creds.request_principal() == ("", "")


def test_missing_expiry_is_not_treated_as_expired():
    """A token stored without expires_at (no expires_in sent) stays usable, matching /status."""
    import asyncio

    auth = provider_auth.OAuthAuth(provider_name="openai", token_url="", client_id="", client_secret="")
    assert asyncio.run(auth._ensure_fresh(None, {"access_token": "at"})) == "at"
