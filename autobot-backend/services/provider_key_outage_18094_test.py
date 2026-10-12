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
    assert "vault unreachable (ConnectionRefusedError)" in caplog.text
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


def _rows_for(*held: str):
    async def _find(_session, name):
        return f"id-{name}" if name in held else None

    return _find


@pytest.mark.asyncio
async def test_root_key_unset_refuses_the_names_the_vault_holds_and_only_those(monkeypatch, caplog) -> None:
    """Contrast: before, an unset root key meant "no vault" and every key came from env."""
    monkeypatch.delenv(ROOT_KEY_ENV, raising=False)
    with (
        patch.object(pkv, "_find_system_secret_id", side_effect=_rows_for(_NAME)),
        patch.object(pkv, "_register_credential_dependency", AsyncMock()),
        caplog.at_level(logging.WARNING),
    ):
        assert await pkv.hydrate_at_startup(_factory_with(MagicMock(commit=AsyncMock()))) == []
    assert pkv._vault_state == pkv.VAULT_REACHABLE
    with pytest.raises(pkv.VaultKeyUnavailableError, match="could not be read"):
        pkv.resolve_provider_key(_NAME, _ENV_VALUE)
    assert pkv.resolve_provider_key("MISTRAL_API_KEY", "mistral-env") == "mistral-env"  # pragma: allowlist secret
    assert "cannot decrypt (RuntimeError)" in caplog.text and _ENV_VALUE not in caplog.text


@pytest.mark.asyncio
async def test_a_deleted_vault_row_leaves_no_stale_cached_value(monkeypatch) -> None:
    monkeypatch.setenv(ROOT_KEY_ENV, "configured")
    pkv._hydrated_keys[_NAME] = "stale-vault-value"  # pragma: allowlist secret
    with (
        patch("services.envelope_secrets_service.EnvelopeSecretsService", autospec=True),
        patch.object(pkv, "_find_system_secret_id", AsyncMock(return_value=None)),
    ):
        await pkv.hydrate_at_startup(_factory_with(MagicMock(commit=AsyncMock())))
    assert _NAME not in pkv._hydrated_keys
    assert pkv.resolve_provider_key(_NAME, _ENV_VALUE) == _ENV_VALUE


@pytest.mark.asyncio
async def test_hydration_swaps_the_cache_atomically(monkeypatch) -> None:
    """A reader during hydration sees the previous complete cache, never a half-filled one."""
    monkeypatch.setenv(ROOT_KEY_ENV, "configured")
    previous = {"MISTRAL_API_KEY": "old"}  # pragma: allowlist secret
    monkeypatch.setattr(pkv, "_hydrated_keys", previous)
    seen = []

    async def _find(_session, name):
        seen.append(pkv._hydrated_keys is previous)
        return "id" if name == _NAME else None

    service = MagicMock(read=AsyncMock(return_value=b"new-vault-value"))
    with (
        patch("services.envelope_secrets_service.EnvelopeSecretsService", return_value=service),
        patch.object(pkv, "_find_system_secret_id", side_effect=_find),
        patch.object(pkv, "_register_credential_dependency", AsyncMock()),
    ):
        assert await pkv.hydrate_at_startup(_factory_with(MagicMock(commit=AsyncMock()))) == [_NAME]
    assert all(seen) and pkv._hydrated_keys == {_NAME: "new-vault-value"}


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


def test_an_unhydrated_process_with_a_root_key_hydrates_before_resolving(monkeypatch) -> None:
    """Contrast: before, a process outside the lifespan (a Celery worker) served env unread."""
    from services import provider_key_process_init as init

    monkeypatch.setenv(ROOT_KEY_ENV, "configured")
    calls = []

    def _outage():
        calls.append(1)
        pkv._vault_state = pkv.VAULT_UNREACHABLE
        return []

    monkeypatch.setattr(init, "hydrate_this_process", _outage)
    with pytest.raises(pkv.VaultKeyUnavailableError, match="could not be reached"):
        pkv.resolve_provider_key(_NAME, _ENV_VALUE)
    assert calls == [1]


def test_a_lazily_hydrated_vault_value_wins(monkeypatch) -> None:
    from services import provider_key_process_init as init

    monkeypatch.setenv(ROOT_KEY_ENV, "configured")

    def _hydrate():
        pkv._hydrated_keys[_NAME] = "vault-value"  # pragma: allowlist secret
        pkv._vault_state = pkv.VAULT_REACHABLE
        return [_NAME]

    monkeypatch.setattr(init, "hydrate_this_process", _hydrate)
    assert pkv.resolve_provider_key(_NAME, _ENV_VALUE) == "vault-value"


def test_without_a_root_key_an_unhydrated_process_uses_env_and_does_not_touch_the_db(monkeypatch) -> None:
    """The stated residual: no root key and no hydration means nothing can say which names are held."""
    from services import provider_key_process_init as init

    monkeypatch.delenv(ROOT_KEY_ENV, raising=False)
    monkeypatch.setattr(init, "hydrate_this_process", lambda: pytest.fail("must not hydrate without a root key"))
    assert pkv.resolve_provider_key(_NAME, _ENV_VALUE) == _ENV_VALUE


def test_the_private_engine_path_classifies_a_db_failure_as_an_outage(monkeypatch) -> None:
    from services import provider_key_process_init as init

    def _no_db():
        raise ConnectionRefusedError("db down")

    monkeypatch.setattr("user_management.database.get_deployment_config", _no_db)
    assert init.hydrate_this_process() == []
    assert pkv._vault_state == pkv.VAULT_UNREACHABLE
    assert init.hydrate_this_process() == []  # once per process: no second attempt


@pytest.mark.asyncio
async def test_hydrate_this_process_is_safe_inside_a_running_loop(monkeypatch) -> None:
    from services import provider_key_process_init as init

    monkeypatch.setattr("user_management.database.get_deployment_config", _db_down)
    assert init.hydrate_this_process() == []
    assert pkv._vault_state == pkv.VAULT_UNREACHABLE


def test_the_celery_worker_hydrates_on_process_init() -> None:
    """``celery_app`` is stubbed under test (conftest, #7766), so the hook is checked in the source."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "celery_app.py").read_text(encoding="utf-8"))
    hooks = [
        fn
        for fn in tree.body
        if isinstance(fn, ast.FunctionDef)
        and any(ast.unparse(d) == "worker_process_init.connect" for d in fn.decorator_list)
        and any(isinstance(c, ast.Call) and ast.unparse(c.func) == "hydrate_this_process" for c in ast.walk(fn))
    ]
    assert len(hooks) == 1


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
