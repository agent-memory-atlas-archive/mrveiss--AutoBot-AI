# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Routing helpers for OpenAI-compatible chat completions."""

import inspect
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, AsyncIterator, Awaitable, Callable

from llm_shared.models import LLMRequest, LLMResponse
from llm_shared.tiered_routing.base_strategy import RoutingStrategy
from llm_shared.tiered_routing.complexity_router import ComplexityRouter

ProviderSelector = Callable[[Any, LLMRequest], Awaitable[Any]]


def _stream_provider_for_response(response: LLMResponse):
    """Adapt a completed response to the existing OpenAI SSE serializer."""

    async def stream_completion(_request: LLMRequest) -> AsyncIterator[str]:
        if response.content:
            yield response.content

    return SimpleNamespace(provider_name=response.provider, stream_completion=stream_completion)


async def prepare_completion(
    active_router: RoutingStrategy | None,
    llm_request: LLMRequest,
    registry: Any,
    select_provider: ProviderSelector,
) -> tuple[LLMResponse | None, Any]:
    """Apply default-strategy escalation, then resolve the provider fallback."""
    if isinstance(active_router, ComplexityRouter):
        escalation_request = replace(
            llm_request,
            messages=[dict(message) for message in llm_request.messages],
            model_name=None,
            stream=False,
        )
        response = await active_router.route_with_escalation(escalation_request)
        if response is not None:
            return response, _stream_provider_for_response(response)

    provider = await select_provider(registry, llm_request)
    if not inspect.isasyncgenfunction(provider.stream_completion):
        raise ValueError(f"Provider {provider.provider_name!r} stream_completion must be an async generator function")
    return None, provider
