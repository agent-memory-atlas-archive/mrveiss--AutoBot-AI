# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""EGRESS_POLICY: internal-origin allowlist, manager mode, raw-client entry point (#17576, AC4)."""

import socket
from unittest.mock import AsyncMock, MagicMock

import pytest

from autobot_shared import egress_allowlist as al
from autobot_shared.http_client_manager import HTTPClientManager
from autobot_shared.http_egress_guard import (
    EGRESS_POLICY,
    EgressBlockedError,
    assert_egress_policy,
)
from autobot_shared.ssot_config import config

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _clean_state():
    al.reset_internal_origin_state()
    yield
    al.reset_internal_origin_state()


@pytest.fixture
def pinned_internal(monkeypatch):
    """A deterministic single-host layout: one internal origin on loopback."""
    monkeypatch.setattr(al, "_ssot_service_urls", lambda: ["http://127.0.0.1:8001", "http://127.0.0.1:11434"])


# --- normalisation -----------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("HTTP://Example.COM/a/b?q=1", "http://example.com:80"),
        ("https://example.com", "https://example.com:443"),
        ("ws://example.com/x", "ws://example.com:80"),
        ("wss://example.com/x", "wss://example.com:443"),
        ("http://example.com:8080/x", "http://example.com:8080"),
        ("http://[::1]:8001/p", "http://[::1]:8001"),
        ("http://[2001:DB8::1]/p", "http://[2001:db8::1]:80"),
        ("http://" + "u" + ":" + "p" + "@example.com:81/", "http://example.com:81"),
    ],
)
def test_normalize_origin(url, expected):
    assert al.normalize_origin(url) == expected


@pytest.mark.parametrize(
    "url", ["example.com", "localhost:8080", "10.0.0.1:80", "", "/just/a/path", "http://", "http://a:99999", None, 5]
)
def test_normalize_origin_rejects_unusable_input(url):
    assert al.normalize_origin(url) is None


def test_build_and_match_use_the_same_normaliser(pinned_internal):
    assert al.static_internal_origins() == {"http://127.0.0.1:8001", "http://127.0.0.1:11434"}


# --- the SSOT-derived set ----------------------------------------------------


def test_ssot_set_contains_the_configured_service_origins():
    origins = al.static_internal_origins()
    for name in ("backend_url", "ollama_url", "slm_url", "aistack_url", "npu_worker_url"):
        assert al.normalize_origin(getattr(config, name)) in origins, name
    assert al.normalize_origin(f"http://{config.vm.chromadb}:{config.port.chromadb}") in origins


def test_redis_url_is_not_an_http_origin():
    assert al.normalize_origin(config.redis_url) not in al.static_internal_origins()


# --- manager mode ------------------------------------------------------------


@pytest.fixture
def manager(monkeypatch):
    mgr = HTTPClientManager()
    session = MagicMock()
    session.request = AsyncMock(return_value="response")
    monkeypatch.setattr(mgr, "get_session", AsyncMock(return_value=session))
    return mgr, session


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["backend_url", "ollama_url"])
async def test_configured_internal_host_is_allowed_under_policy(manager, name):
    mgr, session = manager
    assert await mgr.request("GET", getattr(config, name) + "/health", guard_egress=EGRESS_POLICY) == "response"
    session.request.assert_awaited_once()


@pytest.mark.asyncio
async def test_loopback_internal_origin_is_allowed_though_url_safety_blocks_loopback(manager, pinned_internal):
    mgr, _ = manager
    await mgr.request("GET", "http://127.0.0.1:8001/api/x", guard_egress=EGRESS_POLICY)
    with pytest.raises(EgressBlockedError):
        await mgr.request("GET", "http://127.0.0.1:8001/api/x", guard_egress=False)


@pytest.mark.asyncio
async def test_public_url_is_allowed_with_mocked_dns(manager, monkeypatch, pinned_internal):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0))]
    )
    mgr, session = manager
    await mgr.request("GET", "https://api.example.test/v1", guard_egress=EGRESS_POLICY)
    session.request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://10.1.2.3:8001/x", "http://192.168.1.10/x"])
async def test_private_non_allowlisted_origin_is_refused(manager, pinned_internal, url):
    mgr, session = manager
    with pytest.raises(EgressBlockedError):
        await mgr.request("GET", url, guard_egress=EGRESS_POLICY)
    session.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_internal_host_on_a_different_port_is_refused(manager, pinned_internal):
    mgr, session = manager
    with pytest.raises(EgressBlockedError):
        await mgr.request("GET", "http://127.0.0.1:9999/x", guard_egress=EGRESS_POLICY)
    session.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_policy_still_refuses_redirects(manager, pinned_internal):
    mgr, _ = manager
    with pytest.raises(ValueError):
        await mgr.request("GET", "http://127.0.0.1:8001/x", guard_egress=EGRESS_POLICY, allow_redirects=True)


def test_default_stays_unguarded():
    import inspect

    assert inspect.signature(HTTPClientManager.request).parameters["guard_egress"].default is None


# --- raw-client entry point --------------------------------------------------


@pytest.mark.asyncio
async def test_assert_egress_policy_matches_manager_mode(pinned_internal, monkeypatch):
    await assert_egress_policy("http://127.0.0.1:8001/x")
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0))]
    )
    await assert_egress_policy("https://api.example.test/v1")
    for url in ("http://10.1.2.3/x", "http://127.0.0.1:9999/x", "http://169.254.169.254/latest"):
        with pytest.raises(EgressBlockedError):
            await assert_egress_policy(url)


# --- providers and the TTL cache --------------------------------------------


@pytest.mark.asyncio
async def test_provider_origins_are_allowed_and_cached_for_the_ttl(pinned_internal, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(al.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(al, "EGRESS_INTERNAL_ORIGINS_TTL_S", 30)
    provider = AsyncMock(return_value=["http://10.9.9.9:8095"])
    al.register_internal_origin_provider(provider)

    await assert_egress_policy("http://10.9.9.9:8095/x")
    await assert_egress_policy("http://10.9.9.9:8095/y")
    assert provider.await_count == 1
    clock[0] += 29
    await al.internal_origins()
    assert provider.await_count == 1
    clock[0] += 2
    await al.internal_origins()
    assert provider.await_count == 2


@pytest.mark.asyncio
async def test_provider_exception_leaves_the_static_set_intact(pinned_internal, caplog):
    al.register_internal_origin_provider(AsyncMock(side_effect=RuntimeError("db down")))
    assert await al.internal_origins() == {"http://127.0.0.1:8001", "http://127.0.0.1:11434"}
    assert "provider" in caplog.text
    await assert_egress_policy("http://127.0.0.1:8001/x")


def test_ttl_constant_is_env_backed_and_registered():
    from autobot_shared.env_registry import REGISTRY

    spec = REGISTRY["AUTOBOT_EGRESS_INTERNAL_ORIGINS_TTL_S"]
    assert spec.default == 60 and spec.type is int
