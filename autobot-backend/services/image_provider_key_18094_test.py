# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Image-generation provider keys resolve through provider_key_vault (#18094).

Pins: the names are registered in the one resolver, precedence stays env then
vault, the resolver handed to the plugin is scoped to image names, and no key
value reaches a log record or an exception message.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import provider_key_vault as pkv

_IMAGE = ("OPENAI_API_KEY", "FLUX_API_KEY", "STABILITY_API_KEY")
_ENV_VALUE = "env-value-d41d8cd9"  # pragma: allowlist secret
_VAULT_VALUE = "vault-value-98f00b20"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    pkv._hydrated_keys.clear()
    for name in _IMAGE:
        monkeypatch.delenv(name, raising=False)
    yield
    pkv._hydrated_keys.clear()


def test_image_names_are_registered_once() -> None:
    assert pkv.IMAGE_PROVIDER_KEY_NAMES == frozenset(_IMAGE)
    assert pkv.IMAGE_PROVIDER_KEY_NAMES <= pkv.VAULT_RESOLVED_CREDENTIAL_NAMES
    assert "OPENAI_API_KEY" in pkv.LLM_PROVIDER_KEY_NAMES


def test_owner_map_covers_the_image_only_names_and_keeps_the_llm_owner() -> None:
    assert pkv._CREDENTIAL_CONSUMERS["FLUX_API_KEY"] == "api.image_generation"  # pragma: allowlist secret
    assert pkv._CREDENTIAL_CONSUMERS["STABILITY_API_KEY"] == "api.image_generation"  # pragma: allowlist secret
    assert pkv._CREDENTIAL_CONSUMERS["OPENAI_API_KEY"] == "llm_shared.provider_registry"  # pragma: allowlist secret


@pytest.mark.parametrize("name", _IMAGE)
def test_env_wins_over_the_vault(monkeypatch, name) -> None:
    monkeypatch.setenv(name, _ENV_VALUE)
    pkv._hydrated_keys[name] = _VAULT_VALUE
    assert pkv.resolve_image_provider_key(name) == _ENV_VALUE


@pytest.mark.parametrize("name", _IMAGE)
def test_vault_answers_when_env_is_unset(name) -> None:
    pkv._hydrated_keys[name] = _VAULT_VALUE
    assert pkv.resolve_image_provider_key(name) == _VAULT_VALUE


@pytest.mark.parametrize("name", _IMAGE)
def test_unset_everywhere_is_the_empty_string(name) -> None:
    assert pkv.resolve_image_provider_key(name) == ""


@pytest.mark.asyncio
async def test_a_vault_only_key_is_hydrated_and_resolves() -> None:
    """End to end through hydration with the envelope service autospecced."""
    from services.envelope_secrets_service import EnvelopeSecretsService

    service = MagicMock(spec=EnvelopeSecretsService, instance=True)
    service.read = AsyncMock(return_value=_VAULT_VALUE.encode("utf-8"))
    session = MagicMock()
    session.commit = AsyncMock()

    async def _only_flux(_session, name):
        return "secret-id" if name == "FLUX_API_KEY" else None

    with (
        patch("services.envelope_secrets_service.EnvelopeSecretsService", autospec=True, return_value=service),
        patch.object(pkv, "_find_system_secret_id", side_effect=_only_flux),
        patch.object(pkv, "_register_credential_dependency", AsyncMock()),
    ):
        hydrated = await pkv.hydrate_provider_keys_from_vault(session, root_key=b"k" * 32)
    assert hydrated == ["FLUX_API_KEY"]
    assert pkv.resolve_image_provider_key("FLUX_API_KEY") == _VAULT_VALUE


@pytest.mark.parametrize("name", ["AWS_SECRET_ACCESS_KEY", "ANTHROPIC_API_KEY", "SLACK_BOT_TOKEN", "", "flux_api_key"])
def test_the_scoped_resolver_refuses_non_image_names(monkeypatch, name) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", _ENV_VALUE)
    pkv._hydrated_keys["ANTHROPIC_API_KEY"] = _VAULT_VALUE
    with pytest.raises(ValueError) as exc:
        pkv.resolve_image_provider_key(name)
    assert _ENV_VALUE not in str(exc.value) and _VAULT_VALUE not in str(exc.value)


def test_the_plugin_injection_map_hands_over_only_the_scoped_resolver() -> None:
    assert pkv.plugin_credential_resolvers() == {"image-generation-plugin": pkv.resolve_image_provider_key}


def test_no_key_value_reaches_the_log(monkeypatch, caplog) -> None:
    monkeypatch.setenv("FLUX_API_KEY", _ENV_VALUE)
    pkv._hydrated_keys["STABILITY_API_KEY"] = _VAULT_VALUE
    with caplog.at_level(logging.DEBUG):
        pkv.resolve_image_provider_key("FLUX_API_KEY")
        pkv.resolve_image_provider_key("STABILITY_API_KEY")
        pkv.resolve_image_provider_key("OPENAI_API_KEY")
        with pytest.raises(ValueError):
            pkv.resolve_image_provider_key("AWS_SECRET_ACCESS_KEY")
    assert _ENV_VALUE not in caplog.text and _VAULT_VALUE not in caplog.text


@pytest.mark.asyncio
async def test_list_providers_resolves_through_the_vault_and_hides_the_value(caplog) -> None:
    from api import image_generation as api

    pkv._hydrated_keys["FLUX_API_KEY"] = _VAULT_VALUE
    with caplog.at_level(logging.DEBUG):
        response = await api.list_providers.__wrapped__(current_user=None)
    by_name = {p.name: p for p in response.providers}
    assert by_name["flux"].available is True and by_name["flux"].reason is None
    assert by_name["dalle"].available is False and by_name["dalle"].reason == "OPENAI_API_KEY not set"
    assert _VAULT_VALUE not in response.model_dump_json() and _VAULT_VALUE not in caplog.text


@pytest.mark.asyncio
async def test_list_providers_env_still_wins(monkeypatch) -> None:
    from api import image_generation as api

    monkeypatch.setenv("OPENAI_API_KEY", _ENV_VALUE)
    pkv._hydrated_keys["OPENAI_API_KEY"] = _VAULT_VALUE
    with patch.object(api, "resolve_image_provider_key", wraps=pkv.resolve_image_provider_key) as spy:
        response = await api.list_providers.__wrapped__(current_user=None)
    assert {p.name: p.available for p in response.providers}["dalle"] is True
    assert spy.call_count == 3
