# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Record an :class:`LLMResponse`'s token usage against the cost tracker.

Extracted from ``LLMService._track_usage`` (#16845), which called
``LLMCostTracker.record(provider=..., model=..., usage=..., conversation_id=...)``
-- a method the tracker has never defined. Every call raised ``AttributeError``
into ``except Exception: logger.debug(...)``, so from ``eda3f4529e`` (2026-04-01)
the main chat path recorded no spend at all and said so only at debug level.
Budget Policy's hard stop reads the records written here, so it could not fire
for chat traffic either.

This lives in its own module rather than on ``LLMService`` so the OpenAI- and
Anthropic-compatible gateways record through the same path instead of growing a
third copy of the conversion.

**Token key contract.** ``LLMResponse.usage`` is OpenAI-shaped:
``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``. Providers speaking
another dialect normalise before building the response -- Ollama maps
``prompt_eval_count``/``eval_count`` in
``llm_shared/providers/ollama_provider.py`` -- and every other reader in the tree
assumes the same two keys, e.g.
``llm_shared/observability/prometheus_observer.py``. Reading any other key here
would record zeros while looking fixed.

**Three outcomes stay distinguishable**, because collapsing them is exactly how
the original defect stayed invisible for five months:

* no usage block at all -- the provider reported nothing; normal, silent.
* a usage block carrying neither token key -- a provider contract change; warned.
* the tracker call failed -- a defect; logged with a traceback at error level.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Awaitable, Dict, List, Set

from autobot_shared.logging_manager import get_logger
from autobot_shared.token_count import estimate_fast
from llm_shared.models import LLMRequest, LLMResponse
from services.llm_cost_tracker import get_cost_tracker

logger = get_logger(__name__)

INPUT_TOKEN_KEY = "prompt_tokens"  # nosec B105  # usage-dict key, not a credential
OUTPUT_TOKEN_KEY = "completion_tokens"  # nosec B105  # usage-dict key, not a credential

# The compat gateways' streaming paths have no provider-reported counts and
# approximate from text (`_estimate_tokens`, ~1.3 tokens/word). Recording that
# as though the provider had reported it would make estimated and measured
# spend indistinguishable in the ledger, so the record says which it is.
ESTIMATED_TOKENS = {"token_source": "estimated"}  # nosec B105  # provenance marker, not a credential
MEASURED_TOKENS = {"token_source": "provider_reported"}  # nosec B105  # provenance marker, not a credential

# #16924: where a streamed request carries who the spend belongs to. It rides on
# ``LLMRequest.metadata`` so callers going through ``LLMService.stream()`` --
# which builds the request itself -- attribute spend the same way as callers
# holding a provider directly.
SPEND_ATTRIBUTION_KEY = "spend_attribution"

# The error_message on a stream whose consumer went away before it ended.
STREAM_ABANDONED = "stream abandoned before completion"

# Recording tasks still running after their stream was cancelled; see
# ``_record_despite_cancellation``.
_PENDING_RECORDS: Set[asyncio.Task] = set()


def token_counts(usage: Dict[str, Any]) -> tuple[int, int] | None:
    """``(input, output)`` from a usage block, or ``None`` when it has neither key.

    A block carrying only one of the two is still usable -- the missing side is
    genuinely zero for that call -- so only the both-absent case is unreadable.
    """
    input_tokens = usage.get(INPUT_TOKEN_KEY)
    output_tokens = usage.get(OUTPUT_TOKEN_KEY)
    if input_tokens is None and output_tokens is None:
        return None
    return int(input_tokens or 0), int(output_tokens or 0)


async def record_token_usage(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    session_id: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    endpoint: str | None = None,
    metadata: Dict[str, Any] | None = None,
    success: bool = True,
    error_message: str | None = None,
) -> None:
    """Persist already-extracted token counts so cost and budget policy see them.

    The entry point for callers holding loose counts rather than an
    ``LLMResponse`` -- the compat gateways' streaming paths, which assemble
    usage as chunks arrive (#16845).

    Best-effort with respect to the caller: a tracker failure is logged, never
    raised, since it must not turn a served completion into an error. It is
    logged at error level with a traceback -- the point of #16845 is that this
    failing has to be visible to someone, which ``logger.debug`` was not.
    """
    try:
        await get_cost_tracker().track_usage(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            endpoint=endpoint,
            latency_ms=None,
            success=success,
            error_message=error_message,
            metadata=metadata,
        )
    except Exception:
        logger.exception(
            "Cost tracking failed for %s/%s: %d in / %d out unrecorded, and budget policy cannot see this spend",
            provider or "unknown",
            model or "unknown",
            input_tokens,
            output_tokens,
        )


async def record_response_usage(
    response: LLMResponse,
    session_id: str | None = None,
    *,
    user_id: str | None = None,
    agent_id: str | None = None,
    endpoint: str | None = None,
    model: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> None:
    """Persist ``response``'s token usage so cost and budget policy can see it.

    ``model`` overrides ``response.model`` for callers that priced the call
    under a resolved name -- the compat gateways price with ``resolved_model``,
    and a record naming a different model than the cost was computed from is
    not reconcilable afterwards.
    """
    usage = response.usage or {}
    if not usage:
        return
    counts = token_counts(usage)
    if counts is None:
        logger.warning(
            "Usage block from %s/%s carries neither %s nor %s (keys: %s); no spend recorded",
            response.provider or "unknown",
            response.model or "unknown",
            INPUT_TOKEN_KEY,
            OUTPUT_TOKEN_KEY,
            sorted(usage),
        )
        return
    input_tokens, output_tokens = counts
    try:
        await get_cost_tracker().track_usage(
            provider=response.provider,
            model=model or response.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            session_id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            endpoint=endpoint,
            latency_ms=response.processing_time * 1000 if response.processing_time else None,
            success=not response.error,
            error_message=response.error,
            metadata=metadata,
        )
    except Exception:
        logger.exception(
            "Cost tracking failed for %s/%s: %d in / %d out unrecorded, and budget policy cannot see this spend",
            response.provider or "unknown",
            (model or response.model) or "unknown",
            input_tokens,
            output_tokens,
        )


def spend_attribution(*, user_id: str | None, endpoint: str, **context: Any) -> Dict[str, Any]:
    """``LLMRequest.metadata`` naming who a streamed call's spend belongs to (#16924).

    ``context`` (a recording id, say) is kept on the usage record's metadata so
    spend can be traced to the object it was spent on, not only to the user.
    """
    return {SPEND_ATTRIBUTION_KEY: {"user_id": user_id, "endpoint": endpoint, **context}}


def _model_of(provider: Any, request: LLMRequest) -> str:
    """The model a provider serves ``request`` with: the request's, else the provider's default.

    Mirrors the providers' own ``request.model_name or default_model`` resolution;
    settings are a dict on most providers and an object on the Ollama one.
    """
    if request.model_name:
        return request.model_name
    settings = getattr(provider, "settings", None)
    default = settings.get("default_model") if isinstance(settings, dict) else getattr(settings, "default_model", None)
    return str(default or "")


def _prompt_text(request: LLMRequest) -> str:
    """The request's message text for token estimation; list-form parts contribute only their text.

    ``message_text`` is the shared reader (#14335): a vision or multi-part message
    would otherwise be stringified into its Python repr and inflate the count.
    Imported here because the ``chat_history`` package import is heavy.
    """
    from chat_history.message_schema import message_text

    return "\n".join(message_text(message) for message in request.messages if isinstance(message, dict))


def _stream_outcome(completed: bool, error: Exception | None) -> tuple[bool, str | None]:
    """``(success, error_message)``: only a stream that ran to its end succeeded.

    An abandoned stream -- client gone, task cancelled -- raises ``GeneratorExit``
    or ``CancelledError``, which are not ``Exception``, so it is told apart by
    never having completed rather than by a caught error.
    """
    if error is not None:
        return False, str(error)
    return (True, None) if completed else (False, STREAM_ABANDONED)


async def _record_streamed(
    provider: Any, request: LLMRequest, chunks: List[str], session_id: str | None, outcome: tuple[bool, str | None]
) -> None:
    """Record one ended stream's estimated tokens against whoever the request names."""
    attribution = dict(request.metadata.get(SPEND_ATTRIBUTION_KEY) or {})
    user_id, endpoint = attribution.pop("user_id", None), attribution.pop("endpoint", None)
    if endpoint and user_id is None:
        logger.warning("Spend on %s recorded with no user: the caller attributed no identity", endpoint)
    await record_token_usage(
        provider=getattr(provider, "provider_name", "") or "",
        model=_model_of(provider, request),
        input_tokens=estimate_fast(_prompt_text(request)),
        output_tokens=estimate_fast("".join(chunks)),
        session_id=session_id,
        user_id=user_id,
        endpoint=endpoint,
        metadata={**ESTIMATED_TOKENS, **attribution},
        success=outcome[0],
        error_message=outcome[1],
    )


async def _record_despite_cancellation(record: Awaitable[None]) -> None:
    """Await ``record`` so that a second cancellation cannot drop it.

    ``asyncio.shield`` lets the recording task finish when the waiting stream is
    cancelled again; the task is held in ``_PENDING_RECORDS`` until done, since
    the event loop keeps only weak references to tasks.
    """
    task = asyncio.ensure_future(record)
    _PENDING_RECORDS.add(task)
    task.add_done_callback(_PENDING_RECORDS.discard)
    await asyncio.shield(task)


async def recorded_stream(provider: Any, request: LLMRequest, *, session_id: str | None = None) -> AsyncIterator[str]:
    """``provider.stream_completion(request)``, recording its spend once when the stream ends (#16924).

    The one sanctioned way to stream from a provider object: the spend guard
    (``services/llm_spend_call_sites_test.py``) fails on a direct call elsewhere.

    ``stream_completion`` yields text only -- no provider reports usage on it --
    so tokens are estimated with the canonical ``estimate_fast`` and marked
    ``ESTIMATED_TOKENS``. Recorded on every exit once a chunk has arrived, each
    of which spent the tokens already generated: normal end (``success=True``),
    a provider error mid-stream, or the consumer going away (both
    ``success=False``). A stream that yielded nothing records nothing: a
    rejected or rate-limited attempt is not spend, and ``LLMService.stream()``
    retries those on another model.
    """
    chunks: List[str] = []
    completed, error = False, None
    try:
        async for chunk in provider.stream_completion(request):
            chunks.append(chunk)
            yield chunk
        completed = True
    except Exception as exc:
        error = exc
        raise
    finally:
        if chunks:
            outcome = _stream_outcome(completed, error)
            await _record_despite_cancellation(_record_streamed(provider, request, chunks, session_id, outcome))
