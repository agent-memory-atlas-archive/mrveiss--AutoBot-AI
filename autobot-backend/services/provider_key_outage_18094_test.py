# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A vault outage refuses every key; only "reachable, key absent" or "no vault configured" uses env (#18094).

Contrast with the behaviour before the owner ruling: startup hydration failing was logged at
debug as "non-critical" and every key then resolved from env, including a rotated-out one.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autobot_shared.secrets_envelope import ROOT_KEY_ENV
from services import provider_key_vault as pkv

_ENV_VALUE = "env-maybe-rotated-9c1185a5"  # pragma: allowlist secret
_NAME = "GROQ_API_KEY"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(pkv, "_hydrated_keys", {})
    monkeypatch.setattr(pkv, "_unreadable_keys", set())
    monkeypatch.setattr(pkv, "_env_fallback_logged", set())
    monkeypatch.setattr(pkv, "_vault_state", pkv.VAULT_NOT_HYDRATED)


def _db_down():
    raise ConnectionRefusedError("db down")


def _factory_with(session):
    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    return lambda: (lambda: _Ctx())


@pytest.mark.asyncio
async def test_an_outage_refuses_every_key_even_with_env_present(monkeypatch, caplog) -> None:
    monkeypatch.setenv(ROOT_KEY_ENV, "configured")
    with caplog.at_level(logging.WARNING):
        assert await pkv.hydrate_at_startup(_db_down) == []
        with pytest.raises(pkv.VaultKeyUnavailableError) as exc:
            pkv.resolve_provider_key(_NAME, _ENV_VALUE)
    assert pkv._vault_state == pkv.VAULT_UNREACHABLE
    assert "could not be reached" in str(exc.value)
    assert "vault unreachable at startup (ConnectionRefusedError)" in caplog.text
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert _ENV_VALUE not in caplog.text and _ENV_VALUE not in str(exc.value)


@pytest.mark.asyncio
async def test_a_failure_inside_hydration_is_an_outage_too(monkeypatch) -> None:
    monkeypatch.setenv(ROOT_KEY_ENV, "configured")
    session = MagicMock(commit=AsyncMock())
    with (
        patch("services.envelope_secrets_service.EnvelopeSecretsService", autospec=True),
        patch.object(pkv, "_find_system_secret_id", AsyncMock(side_effect=OSError("query failed"))),
    ):
        assert await pkv.hydrate_at_startup(_factory_with(session)) == []
    with pytest.raises(pkv.VaultKeyUnavailableError):
        pkv.resolve_provider_key(_NAME, _ENV_VALUE)


@pytest.mark.asyncio
async def test_no_vault_configured_resolves_from_env_with_a_warning(monkeypatch, caplog) -> None:
    monkeypatch.delenv(ROOT_KEY_ENV, raising=False)
    with caplog.at_level(logging.WARNING):
        assert await pkv.hydrate_at_startup(_db_down) == []
        assert pkv.resolve_provider_key(_NAME, _ENV_VALUE) == _ENV_VALUE
    assert pkv._vault_state == pkv.VAULT_NOT_CONFIGURED
    assert "no vault configured" in caplog.text


@pytest.mark.asyncio
async def test_a_reachable_vault_without_the_key_falls_back_to_env(monkeypatch, caplog) -> None:
    monkeypatch.setenv(ROOT_KEY_ENV, "configured")
    session = MagicMock(commit=AsyncMock())
    with (
        patch("services.envelope_secrets_service.EnvelopeSecretsService", autospec=True),
        patch.object(pkv, "_find_system_secret_id", AsyncMock(return_value=None)),
        caplog.at_level(logging.WARNING),
    ):
        assert await pkv.hydrate_at_startup(_factory_with(session)) == []
        assert pkv.resolve_provider_key(_NAME, _ENV_VALUE) == _ENV_VALUE
    assert pkv._vault_state == pkv.VAULT_REACHABLE
    assert "vault has no key: using the environment" in caplog.text


@pytest.mark.asyncio
async def test_a_successful_hydration_after_an_outage_restores_resolution(monkeypatch) -> None:
    monkeypatch.setenv(ROOT_KEY_ENV, "configured")
    await pkv.hydrate_at_startup(_db_down)
    with (
        patch("services.envelope_secrets_service.EnvelopeSecretsService", autospec=True),
        patch.object(pkv, "_find_system_secret_id", AsyncMock(return_value=None)),
    ):
        await pkv.hydrate_at_startup(_factory_with(MagicMock(commit=AsyncMock())))
    assert pkv.resolve_provider_key(_NAME, _ENV_VALUE) == _ENV_VALUE


def test_a_process_that_never_hydrates_keeps_the_env_fallback() -> None:
    assert pkv._vault_state == pkv.VAULT_NOT_HYDRATED
    assert pkv.resolve_provider_key(_NAME, _ENV_VALUE) == _ENV_VALUE


# --- the readers this round routed through the resolver ------------------------------------------


@pytest.mark.parametrize("name", sorted(pkv.VIDEO_PROVIDER_KEY_NAMES))
def test_video_keys_are_vault_resolved_and_scoped(name) -> None:
    assert name in pkv.VAULT_RESOLVED_CREDENTIAL_NAMES
    pkv._unreadable_keys.add(name)
    with pytest.raises(pkv.VaultKeyUnavailableError):
        pkv.resolve_video_provider_key(name)
    with pytest.raises(ValueError):
        pkv.resolve_video_provider_key("OPENAI_API_KEY")


def test_tilde_reports_the_refusal_instead_of_using_env(monkeypatch) -> None:
    from voice_processing.providers import ProviderUnavailable
    from voice_processing.providers.lv.tilde_provider import TildeProvider

    monkeypatch.setenv("TILDE_API_KEY", _ENV_VALUE)
    pkv._unreadable_keys.add("TILDE_API_KEY")
    provider = TildeProvider()
    assert provider.api_key is None

    import asyncio

    with pytest.raises(ProviderUnavailable, match="vault unavailable"):
        asyncio.run(provider.transcribe("/nonexistent.wav"))
