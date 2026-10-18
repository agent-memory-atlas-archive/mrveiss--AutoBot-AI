# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""
Integration tests for the openai_compat rate-limit wiring (#7271).

Per-IP rate-limit logic itself lives in
``autobot_shared.rate_limit.IPRateLimiter`` and is comprehensively tested
in ``autobot_shared/rate_limit_test.py`` (covers Redis path, in-process
fallback, env-driven limit, etc.).

This file pins the openai_compat-specific configuration of that helper:
the right key prefix, the right env var, and that the helper is the same
instance referenced by the request handler.

Pre-#7271 this file tested the standalone ``_check_oai_rate_limit``
function shipped in #6588. That function was extracted into the shared
helper, so the tests are now at the helper-level and the integration
tests below pin the per-endpoint configuration.
"""

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api import openai_compat
from autobot_shared.rate_limit import IPRateLimiter


# #13113: asyncio.run() — pytest-asyncio owns the loop lifecycle, so a sync test
# running before any async test on its worker had no current loop for get_event_loop().
class TestGetUserAsync:
    """MVA-169: _get_user must be async and await get_current_user."""

    def test_get_user_is_coroutine_function(self):
        assert inspect.iscoroutinefunction(openai_compat._get_user)

    def test_get_user_awaits_get_current_user(self):
        fake_request = MagicMock()
        fake_user = {"id": "u1", "email": "test@example.com"}

        async def run():
            with patch(
                "api.openai_compat.get_current_user", new_callable=AsyncMock, return_value=fake_user
            ) as mock_gcu:
                result = await openai_compat._get_user(fake_request)
                mock_gcu.assert_awaited_once_with(fake_request)
                assert result == fake_user

        asyncio.run(run())

    def test_get_user_propagates_http_exception(self):
        fake_request = MagicMock()

        async def run():
            with patch(
                "api.openai_compat.get_current_user",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=401, detail="Unauthorized"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await openai_compat._get_user(fake_request)
                assert exc_info.value.status_code == 401

        asyncio.run(run())

    def test_get_user_converts_unexpected_exception_to_401(self):
        fake_request = MagicMock()

        async def run():
            with patch(
                "api.openai_compat.get_current_user",
                new_callable=AsyncMock,
                side_effect=RuntimeError("jwt decode failed"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await openai_compat._get_user(fake_request)
                assert exc_info.value.status_code == 401
                assert exc_info.value.detail == "Unauthorized"

        asyncio.run(run())


class TestOAIRateLimitWiring:
    """Issue #7271: openai_compat uses the shared IPRateLimiter."""

    def test_module_exposes_limiter_instance(self):
        """``_oai_limiter`` must be an IPRateLimiter (not the legacy func)."""
        assert isinstance(openai_compat._oai_limiter, IPRateLimiter)

    def test_limiter_uses_oai_specific_config(self):
        """The instance is configured for the /v1/chat/completions endpoint."""
        limiter = openai_compat._oai_limiter
        # #6588 / #7271: prefix shared across uvicorn workers via Redis key.
        assert limiter._key_prefix == "ratelimit:oai"
        # AUTOBOT_OAI_RATE_LIMIT env var; default 60/min preserved.
        assert limiter._limit_env == "AUTOBOT_OAI_RATE_LIMIT"
        assert limiter._default_limit == 60
        assert limiter._window_seconds == 60


class TestAutoModelRouting:
    def test_chat_completion_uses_configured_cost_strategy(self, monkeypatch):
        import llm_shared.tiered_routing.cost_router as cost_router_module
        from llm_shared.models import LLMResponse
        from llm_shared.tiered_routing import registry
        from llm_shared.tiered_routing.cost_router import CostRouter
        from llm_shared.tiered_routing.tier_config import TierConfig, TierModels

        config = TierConfig(models=TierModels(simple="cheap-model", complex="capable-model"))
        route_calls = []
        provider_requests = []
        original_route = CostRouter.route

        def observe_route(router, messages, requested_model=None):
            route_calls.append((router, messages, requested_model))
            return original_route(router, messages, requested_model=requested_model)

        def blended_cost(model):
            return {"cheap-model": 1, "capable-model": 10}[model]

        monkeypatch.setattr(registry, "_active_router", None)
        monkeypatch.setattr(registry.TierConfig, "from_config", classmethod(lambda cls: config))
        monkeypatch.setenv("AUTOBOT_LLM_ROUTING_STRATEGY", "cost")
        monkeypatch.setattr(cost_router_module, "_blended_cost", blended_cost)
        monkeypatch.setattr(CostRouter, "route", observe_route)

        async def stream_completion(_request):
            yield "unused"

        provider = MagicMock()
        provider.stream_completion = stream_completion
        provider_registry = MagicMock()

        async def get_provider_for_request(*, request):
            provider_requests.append(request)
            return provider

        provider_registry.get_provider_for_request = AsyncMock(side_effect=get_provider_for_request)
        fallback_coordinator = MagicMock()
        fallback_coordinator.execute_with_fallback = AsyncMock(
            return_value=LLMResponse(content="answer", usage={}, hidden_params={})
        )
        cost_tracker = MagicMock()
        cost_tracker.calculate_cost.return_value = 0.0

        monkeypatch.setattr(openai_compat, "_resolve_auth", AsyncMock(return_value=({"id": "user"}, None)))
        monkeypatch.setattr(openai_compat._oai_limiter, "check_or_429", AsyncMock())
        monkeypatch.setattr(openai_compat, "_remote_addr", lambda _request: "127.0.0.1")
        monkeypatch.setattr(openai_compat, "_enforce_virtual_key", AsyncMock())
        monkeypatch.setattr(openai_compat, "get_provider_registry", lambda: provider_registry)
        monkeypatch.setattr(openai_compat, "get_fallback_coordinator", lambda: fallback_coordinator)
        monkeypatch.setattr(openai_compat, "get_cost_tracker", lambda: cost_tracker)
        monkeypatch.setattr(openai_compat, "record_response_usage", AsyncMock())

        async def make_completion():
            body = openai_compat.ChatCompletionRequest(
                model="auto",
                messages=[{"role": "user", "content": "What is 2 + 2?"}],
            )
            return await openai_compat.chat_completions(body, MagicMock())

        response = asyncio.run(make_completion())

        assert response.status_code == 200
        assert provider_requests[0].model_name == "cheap-model"
        assert len(route_calls) == 1
        assert isinstance(route_calls[0][0], CostRouter)
        assert route_calls[0][1:] == ([{"role": "user", "content": "What is 2 + 2?"}], "auto")

    @pytest.mark.parametrize("stream", [False, True])
    def test_default_complexity_strategy_keeps_claude_escalation(self, monkeypatch, stream):
        import json

        from llm_shared.models import LLMResponse
        from llm_shared.tiered_routing import registry
        from llm_shared.tiered_routing.complexity_router import ComplexityRouter
        from llm_shared.tiered_routing.tier_config import TierConfig, TierModels

        config = TierConfig(models=TierModels(simple="cheap-model", complex="capable-model"))
        active_router = ComplexityRouter(config)
        route = MagicMock(return_value=("capable-model", None))
        escalated_response = LLMResponse(
            content="Claude answer",
            model="claude-sonnet",
            provider="anthropic",
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        )

        async def mutate_escalation_request(request):
            request.messages[0]["content"] = "provider-mutated content"
            return escalated_response

        escalation = AsyncMock(side_effect=mutate_escalation_request)
        built_requests = []
        original_build = openai_compat._build_llm_request

        def observe_build(body, resolved_model=None):
            request = original_build(body, resolved_model=resolved_model)
            built_requests.append(request)
            return request

        monkeypatch.setattr(active_router, "route", route)
        monkeypatch.setattr(active_router, "route_with_escalation", escalation)
        monkeypatch.setattr(registry, "_active_router", active_router)
        monkeypatch.setattr(openai_compat, "_build_llm_request", observe_build)

        provider_registry = MagicMock()
        provider_registry.get_provider_for_request = AsyncMock()
        fallback_coordinator = MagicMock()
        fallback_coordinator.execute_with_fallback = AsyncMock()
        cost_tracker = MagicMock()
        cost_tracker.calculate_cost.return_value = 0.0

        monkeypatch.setattr(openai_compat, "_resolve_auth", AsyncMock(return_value=({"id": "user"}, None)))
        monkeypatch.setattr(openai_compat._oai_limiter, "check_or_429", AsyncMock())
        monkeypatch.setattr(openai_compat, "_remote_addr", lambda _request: "127.0.0.1")
        monkeypatch.setattr(openai_compat, "_enforce_virtual_key", AsyncMock())
        monkeypatch.setattr(openai_compat, "get_provider_registry", lambda: provider_registry)
        monkeypatch.setattr(openai_compat, "get_fallback_coordinator", lambda: fallback_coordinator)
        monkeypatch.setattr(openai_compat, "get_cost_tracker", lambda: cost_tracker)
        monkeypatch.setattr(openai_compat, "record_response_usage", AsyncMock())
        monkeypatch.setattr(openai_compat, "record_token_usage", AsyncMock())

        async def make_completion():
            body = openai_compat.ChatCompletionRequest(
                model="auto",
                messages=[{"role": "user", "content": "Explain this design."}],
                stream=stream,
                stream_options={"include_usage": True} if stream else None,
            )
            response = await openai_compat.chat_completions(body, MagicMock())
            if stream:
                chunks = []
                async for chunk in response.body_iterator:
                    chunks.append(chunk.encode() if isinstance(chunk, str) else chunk)
                return response, b"".join(chunks).decode()
            return response, json.loads(response.body)

        response, body = asyncio.run(make_completion())

        assert response.status_code == 200
        assert route.call_args.args == ([{"role": "user", "content": "Explain this design."}],)
        assert route.call_args.kwargs == {"requested_model": "auto"}
        escalation.assert_awaited_once()
        escalation_request = escalation.await_args.args[0]
        assert escalation_request.model_name is None
        assert escalation_request.stream is False
        assert built_requests[0].messages == [{"role": "user", "content": "Explain this design."}]
        provider_registry.get_provider_for_request.assert_not_awaited()
        fallback_coordinator.execute_with_fallback.assert_not_awaited()
        if stream:
            assert "Claude answer" in body
            assert "data: [DONE]" in body
            stream_chunks = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]
            usage = next(chunk["usage"] for chunk in stream_chunks if chunk.get("usage") is not None)
            assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
            cost_tracker.calculate_cost.assert_called_once_with(
                "claude-sonnet", usage["prompt_tokens"], usage["completion_tokens"]
            )
        else:
            assert body["model"] == "claude-sonnet"
            assert body["choices"][0]["message"]["content"] == "Claude answer"

    def test_explicit_aliases_keep_forced_tiers(self):
        forced_router = MagicMock()
        forced_router.get_model_for_tier.side_effect = {
            "simple": "fast-model",
            "complex": "quality-model",
        }.__getitem__

        async def resolve_aliases():
            with patch("api.openai_compat.get_tiered_router", return_value=forced_router):
                fast = await openai_compat._resolve_auto_model("auto-fast", [])
                quality = await openai_compat._resolve_auto_model("auto-quality", [])
            return fast, quality

        assert asyncio.run(resolve_aliases()) == ("fast-model", "quality-model")
        assert forced_router.get_model_for_tier.call_args_list[0].args == ("simple",)
        assert forced_router.get_model_for_tier.call_args_list[1].args == ("complex",)
