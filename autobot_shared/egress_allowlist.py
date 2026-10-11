# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The internal-origin allowlist behind ``EGRESS_POLICY`` (#17576, AC4).

Rule 8 wants every outbound request guarded; AutoBot's own service-to-service
traffic must still reach hosts that are loopback on a single-host deployment,
which :mod:`autobot_shared.url_safety` hard-blocks in both modes. The policy is
therefore "internal origins bypass the check, everything else must be public",
and this module is the one place that says what *internal* means.

**Exact origin match, never a bare host.** An origin is ``scheme://host:port``
with the port always explicit. A known-internal host on another port is a
different service, possibly a different trust domain, so it is NOT internal.

**One normaliser** (:func:`normalize_origin`) is used both to build the set and
to match against it, so the two sides cannot disagree about case, default ports
or IPv6 brackets.

Static origins come from the SSOT (:data:`autobot_shared.ssot_config.config`).
Dynamic origins (e.g. SLM-managed nodes) arrive through providers registered
with :func:`register_internal_origin_provider`; their result is cached for
``EGRESS_INTERNAL_ORIGINS_TTL_S`` and a failing provider never breaks the
static set.
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Iterable, Optional
from urllib.parse import urlparse

from autobot_shared.env_utils import env_int

logger = logging.getLogger(__name__)

#: Seconds a provider-derived origin set is reused before providers run again.
EGRESS_INTERNAL_ORIGINS_TTL_S: int = env_int("AUTOBOT_EGRESS_INTERNAL_ORIGINS_TTL_S", 60)

_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}

OriginProvider = Callable[[], Awaitable[Iterable[str]]]

_providers: list[OriginProvider] = []
_cache: tuple[float, frozenset[str]] | None = None


def normalize_origin(url: object) -> Optional[str]:
    """``scheme://host:port`` for *url*, or ``None`` when it has no usable origin.

    Scheme and host are lowercased, IPv6 hosts are bracketed, and the port is
    explicit (defaulted by scheme). Path, query and userinfo are ignored. A
    scheme-less string, an unparsable one, or a scheme with neither an explicit
    port nor a known default yields ``None``.
    """
    if not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url.strip())
        scheme, host, port = parsed.scheme.lower(), parsed.hostname, parsed.port
    except ValueError:
        return None
    port = port or _DEFAULT_PORTS.get(scheme)
    if not scheme or not host or not parsed.netloc or port is None:
        return None
    return f"{scheme}://{f'[{host}]' if ':' in host else host}:{port}"


def _normalized(urls: Iterable[object]) -> frozenset[str]:
    return frozenset(o for o in (normalize_origin(u) for u in urls) if o)


def _ssot_service_urls() -> list[str]:
    """Service URLs the SSOT declares as internal (found by reading ``ssot_config``).

    Every ``*_url`` service property on ``AutoBotConfig`` (``backend_url``,
    ``frontend_url``, ``ollama_url``, ``aistack_url``, ``npu_worker_url``,
    ``tts_worker_url``, ``browser_service_url``, ``chrome_cdp_url``, ``vnc_url``,
    ``slm_url``, ``websocket_url``) plus ChromaDB, which has no URL property and
    is built from ``vm.chromadb`` + ``port.chromadb``.

    ``redis_url`` is deliberately absent: it is a ``redis://`` endpoint, never
    the target of HTTP egress, and :func:`normalize_origin` has no default port
    for that scheme.
    """
    from autobot_shared.ssot_config import config

    names = (
        "backend_url",
        "frontend_url",
        "ollama_url",
        "aistack_url",
        "npu_worker_url",
        "tts_worker_url",
        "browser_service_url",
        "chrome_cdp_url",
        "vnc_url",
        "slm_url",
        "websocket_url",
    )
    urls = [getattr(config, name, "") for name in names]
    urls.append(f"http://{config.vm.chromadb}:{config.port.chromadb}")
    return urls


def static_internal_origins() -> frozenset[str]:
    """Origins derived from the SSOT, recomputed on every call (config may reload)."""
    return _normalized(_ssot_service_urls())


def register_internal_origin_provider(provider: OriginProvider) -> None:
    """Add an async callable yielding extra internal URLs/origins (e.g. SLM nodes).

    Registering invalidates the cache so the new provider is consulted at once.
    """
    global _cache
    if provider not in _providers:
        _providers.append(provider)
    _cache = None


async def _provider_origins() -> frozenset[str]:
    global _cache
    now = time.monotonic()
    if _cache is not None and now < _cache[0]:
        return _cache[1]
    found: set[str] = set()
    for provider in list(_providers):
        try:
            found |= _normalized(await provider())
        except Exception:
            logger.warning("Internal-origin provider %r failed; static origins only", provider, exc_info=True)
    result = frozenset(found)
    _cache = (now + EGRESS_INTERNAL_ORIGINS_TTL_S, result)
    return result


async def internal_origins() -> frozenset[str]:
    """Static SSOT origins plus the (TTL-cached) provider origins."""
    return static_internal_origins() | await _provider_origins()


async def is_internal_origin(url: str) -> bool:
    """True when *url*'s exact origin is in :func:`internal_origins`."""
    origin = normalize_origin(url)
    return origin is not None and origin in await internal_origins()


def reset_internal_origin_state() -> None:
    """Drop providers and the cache (test isolation)."""
    global _cache
    _providers.clear()
    _cache = None
