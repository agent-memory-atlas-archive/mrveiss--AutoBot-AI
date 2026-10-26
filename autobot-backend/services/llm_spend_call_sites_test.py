# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Every streamed LLM call records its spend, and a new direct call cannot arrive unnoticed (#16924).

Three live call sites reached a provider and recorded nothing: ``/api/chat/compare``,
the transcript-analysis WebSocket and the transcriber's ``ai/ask``. They now stream
through ``services.llm_usage_recording.recorded_stream`` -- directly (compare) or via
``LLMService.stream()`` (both transcript surfaces), which records too.

The behavioural tests drive each site end to end against a tracker double and fail
if the recording is removed. The guard at the bottom enumerates every call to a
provider method outside the provider layer, so the next unrecorded site fails here
on arrival instead of being found by a later audit.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch

import pytest

from llm_shared.models import LLMRequest
from services.llm_cost_tracker import LLMCostTracker
from services.llm_usage_recording import ESTIMATED_TOKENS, recorded_stream, spend_attribution

_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "autobot-backend"


class _StreamingProvider:
    """Yields fixed chunks; optionally fails after them, the way a dropped connection does."""

    provider_name = "fake"
    settings = {"default_model": "fake-default"}

    def __init__(self, chunks=("Hello ", "world"), fail_after: Exception | None = None) -> None:
        self.chunks = chunks
        self.fail_after = fail_after
        self.requests: list[LLMRequest] = []

    async def stream_completion(self, request: LLMRequest):
        self.requests.append(request)
        for chunk in self.chunks:
            yield chunk
        if self.fail_after is not None:
            raise self.fail_after


class _Registry:
    def __init__(self, provider) -> None:
        self.provider = provider

    async def get_provider_for_request(self, provider_name=None, conversation_id=None, request=None, org_id=None):
        return self.provider


@pytest.fixture
def tracker():
    """A tracker double that accepts only methods the real class defines."""
    double = create_autospec(LLMCostTracker, instance=True)
    with patch("services.llm_usage_recording.get_cost_tracker", return_value=double):
        yield double


def _real_llm_service_module():
    """The genuine ``services/llm_service.py``: the backend conftest stubs ``services.llm_service``.

    Loaded under a private name, as ``tests/services/test_llm_service_fallback_events.py``
    does, so the stub other tests rely on is left in place.
    """
    name = "_real_llm_service_16924"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, _BACKEND / "services" / "llm_service.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.fixture
def service(monkeypatch):
    """A real LLMService over a one-provider registry, so the stream() path is the production one."""
    llm_service_mod = _real_llm_service_module()
    provider = _StreamingProvider()
    svc = llm_service_mod.LLMService(registry=_Registry(provider))
    monkeypatch.setattr(llm_service_mod, "get_fallback_chain_manager", lambda: MagicMock())
    return SimpleNamespace(svc=svc, provider=provider)


def _recorded(tracker) -> dict:
    tracker.track_usage.assert_awaited_once()
    return tracker.track_usage.await_args.kwargs


async def _drain(stream) -> list[str]:
    return [chunk async for chunk in stream]


# ---------------------------------------------------------------------------
# recorded_stream -- the one recorder every streamed site goes through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_finished_stream_records_estimated_tokens_once_against_the_named_caller(tracker) -> None:
    request = LLMRequest(
        messages=[{"role": "user", "content": "count these words"}],
        metadata=spend_attribution(user_id="alice", endpoint="/x", recording_id=7),
    )
    chunks = await _drain(recorded_stream(_StreamingProvider(), request, session_id="conv-1"))

    assert chunks == ["Hello ", "world"]
    kwargs = _recorded(tracker)
    assert (kwargs["provider"], kwargs["model"]) == ("fake", "fake-default")
    assert (kwargs["user_id"], kwargs["endpoint"], kwargs["session_id"]) == ("alice", "/x", "conv-1")
    assert kwargs["input_tokens"] > 0 and kwargs["output_tokens"] > 0
    assert kwargs["metadata"] == {**ESTIMATED_TOKENS, "recording_id": 7}
    assert kwargs["success"] is True


@pytest.mark.asyncio
async def test_a_stream_failing_midway_records_the_spent_tokens_as_unsuccessful_and_reraises(tracker) -> None:
    provider = _StreamingProvider(chunks=("partial",), fail_after=RuntimeError("connection reset"))
    with pytest.raises(RuntimeError, match="connection reset"):
        await _drain(recorded_stream(provider, LLMRequest(messages=[{"role": "user", "content": "hi"}])))

    kwargs = _recorded(tracker)
    assert kwargs["success"] is False and kwargs["error_message"] == "connection reset"


@pytest.mark.asyncio
async def test_a_stream_that_yielded_nothing_records_nothing(tracker) -> None:
    provider = _StreamingProvider(chunks=(), fail_after=RuntimeError("429 rate limit exceeded"))
    with pytest.raises(RuntimeError):
        await _drain(recorded_stream(provider, LLMRequest(messages=[{"role": "user", "content": "hi"}])))

    tracker.track_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_recording_failure_is_logged_loudly_and_the_stream_still_completes(tracker) -> None:
    tracker.track_usage.side_effect = ConnectionError("redis down")
    request = LLMRequest(messages=[{"role": "user", "content": "hi"}])
    with patch("services.llm_usage_recording.logger") as logger:
        chunks = await _drain(recorded_stream(_StreamingProvider(), request))

    assert chunks == ["Hello ", "world"]
    logger.exception.assert_called_once()
    logger.debug.assert_not_called()


@pytest.mark.asyncio
async def test_llm_service_stream_records_against_the_conversation(tracker, service) -> None:
    chunks = await _drain(service.svc.stream([{"role": "user", "content": "hi"}], conversation_id="conv-9"))

    assert chunks == ["Hello ", "world"]
    assert _recorded(tracker)["session_id"] == "conv-9"


# ---------------------------------------------------------------------------
# The three #16924 call sites, end to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_compare_records_each_models_spend_against_the_caller(tracker) -> None:
    from api import chat_compare

    provider = _StreamingProvider()
    with patch("llm_shared.get_provider_registry", return_value=_Registry(provider)):
        stream = chat_compare._stream_single_model("fake/model-a", [{"role": "user", "content": "hi"}], "alice")
        events = await _drain(stream)

    assert events[-1] == chat_compare._sse({"model": "fake/model-a", "done": True})
    kwargs = _recorded(tracker)
    assert (kwargs["user_id"], kwargs["model"], kwargs["endpoint"]) == ("alice", "model-a", "/api/chat/compare")


@pytest.mark.asyncio
async def test_transcript_analysis_records_against_the_caller_and_recording(tracker, service) -> None:
    from api import transcripts
    from api.schemas_transcripts import AnalysisType, TranscriptAnalyzeRequest

    request = TranscriptAnalyzeRequest(analysis_type=AnalysisType.SUMMARIZE)
    with patch.object(transcripts, "get_llm_service", return_value=service.svc):
        tokens = await _drain(transcripts._stream_analysis("A: hi", request, user_id="alice", recording_id="456"))

    assert tokens == ["Hello ", "world"]
    kwargs = _recorded(tracker)
    assert kwargs["user_id"] == "alice" and kwargs["metadata"]["recording_id"] == "456"


@pytest.mark.asyncio
async def test_transcriber_ask_records_against_the_caller_and_recording(tracker, service) -> None:
    from transcriber.models import AiAskRequest
    from transcriber.routes import ai

    db = SimpleNamespace(get_recording=AsyncMock(return_value={"id": 7, "user_id": "alice"}))
    request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id="alice", is_admin=False)))
    with (
        patch.object(ai, "get_llm_service", return_value=service.svc),
        patch.object(ai, "build_segment_list", AsyncMock(return_value=[])),
    ):
        response = await ai.ai_ask(7, AiAskRequest(action="summarize"), request, db)
        body = "".join([chunk async for chunk in response.body_iterator])

    assert "Hello " in body and "error" not in body
    kwargs = _recorded(tracker)
    assert kwargs["user_id"] == "alice" and kwargs["metadata"]["recording_id"] == 7


# ---------------------------------------------------------------------------
# Guard: every provider-invoking call site records, or says why it does not
# ---------------------------------------------------------------------------

_PROVIDER_METHODS = frozenset({"stream_completion", "chat_completion", "stream_chat"})

# The provider layer: one provider or adapter delegating to another. The site
# accountable for recording is whoever called the outermost provider.
_PROVIDER_LAYER = ("autobot-backend/llm_shared/providers/", "autobot-backend/llm_shared/adapters/")

# Sites that record, each naming where.
_RECORDED = {
    "autobot-backend/services/llm_usage_recording.py::recorded_stream": "the recorder itself",
    "autobot-backend/services/llm_service.py::LLMService._attempt_chat_once": "awaits _track_usage (#16845)",
    "autobot-backend/services/llm_service.py::LLMService.chat_optimized": "awaits _track_usage (#16845)",
    "autobot-backend/api/openai_compat.py::_stream_generator": "record_token_usage at stream end (#16845)",
    "autobot-backend/api/anthropic_compat.py::_stream_generator_anthropic": "record_token_usage at stream end (#16845)",
    "autobot-backend/llm_shared/model_fallback_coordinator.py::ModelFallbackCoordinator._dispatch": (
        "only the compat gateways' non-stream paths call it; both record_response_usage the result (#16845)"
    ),
}

# Known unrecorded, tracked rather than fixed here. SHRINK-ONLY: never add an
# entry to make a new site pass -- record through llm_usage_recording instead.
_KNOWN_UNRECORDED = {
    # Below services/, so they cannot import the recorder: tracked in #19650.
    "autobot-backend/llm_shared/tiered_routing/complexity_router.py::ComplexityRouter.route_with_escalation": "#19650",
    "autobot_shared/pre_action_verifier_guard.py::_call_verifier_once": "#19650",
    # chat_completion on an LLMService, which has no such method since #3185: tracked in #19649.
    "autobot-backend/agents/agent_orchestration/routing.py::AgentRouter._determine_routing_base": "#19649",
    "autobot-backend/async_chat_workflow.py::AsyncChatWorkflow._generate_llm_response": "#19649",
    "autobot-backend/modern_ai_integration.py::LocalModelProvider.generate_text": "#19649",
    "autobot-backend/task_handlers/llm_handlers.py::LLMChatCompletionHandler.execute": "#19649",
}

# Raw-HTTP inference no method-name scan can see, exempt by a stated condition.
_OLLAMA_DIRECT = _BACKEND / "chat_workflow" / "manager.py"
_OLLAMA_EXEMPTION = "Spend exemption (#16924)"


class _SiteCollector(ast.NodeVisitor):
    """``path::Qualified.function`` for every ``<obj>.<provider method>(...)`` call in a module."""

    def __init__(self, rel: str) -> None:
        self.rel, self.scope, self.sites = rel, [], set()

    def _enter(self, node) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _enter

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr in _PROVIDER_METHODS:
            self.sites.add(f"{self.rel}::{'.'.join(self.scope) or '<module>'}")
        self.generic_visit(node)


def _sites_in(source: str, rel: str) -> set[str]:
    collector = _SiteCollector(rel)
    collector.visit(ast.parse(source))
    return collector.sites


def _is_production(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    test_file = name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
    return not (test_file or "/tests/" in f"/{rel}" or rel.startswith(_PROVIDER_LAYER))


def _production_sites() -> set[str]:
    sites: set[str] = set()
    for root in (_BACKEND, _REPO / "autobot_shared"):
        for path in root.rglob("*.py"):
            rel = path.relative_to(_REPO).as_posix()
            if _is_production(rel):
                sites |= _sites_in(path.read_text(encoding="utf-8"), rel)
    return sites


def test_the_collector_sees_a_direct_provider_call_and_names_its_function() -> None:
    """Control: an empty result below must mean "no unrecorded site", not "the parser saw nothing"."""
    source = "class Svc:\n    async def run(self, provider, req):\n        return await provider.chat_completion(req)\n"
    assert _sites_in(source, "pkg/mod.py") == {"pkg/mod.py::Svc.run"}


def test_every_provider_call_site_records_or_is_listed() -> None:
    unlisted = _production_sites() - _RECORDED.keys() - _KNOWN_UNRECORDED.keys()
    assert not unlisted, (
        f"Direct provider calls with no spend recording: {sorted(unlisted)}. Stream through "
        "services.llm_usage_recording.recorded_stream or LLMService, or record the response "
        "with record_response_usage (#16924)."
    )


def test_no_listed_site_has_gone_stale() -> None:
    """A listed site that no longer calls a provider is removed from the list, so the list cannot rot."""
    stale = (_RECORDED.keys() | _KNOWN_UNRECORDED.keys()) - _production_sites()
    assert not stale, f"Listed but no longer a provider call site; delete the entry: {sorted(stale)}"


def test_the_ollama_direct_workflow_states_its_spend_exemption() -> None:
    assert _OLLAMA_EXEMPTION in _OLLAMA_DIRECT.read_text(encoding="utf-8")
