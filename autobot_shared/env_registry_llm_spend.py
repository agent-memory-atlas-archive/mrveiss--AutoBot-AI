# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""LLM spend-recording and transcript-analysis AUTOBOT_* environment variable registrations (#16924).

A sibling of ``env_registry_backend_services`` (which imports this module, because it sits
near the file-size ceiling and ``env_registry.py`` is closed to new entries), following the
``env_registry_ledger`` precedent; the registration contract is the one ``env_registry``
states (#16415).
"""

from __future__ import annotations

from autobot_shared.env_registry import EnvVarSpec, register_env_var

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_TRANSCRIPT_ANALYSIS_MAX_TOKENS",
        type=int,
        default=4096,
        description=(
            "Output-token budget for one transcript analysis: the transcripts analysis WebSocket "
            "and the transcriber ai/ask route (transcriber/ai/prompts.py, #16924). Set explicitly "
            "because LLMService.stream() otherwise applies the general task default of 1024, which "
            "cut long summaries and protocols off mid-answer."
        ),
        component="backend",
        range=(256, 32768),
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_LLM_SPEND_RECORD_TIMEOUT_SECONDS",
        type=float,
        default=10.0,
        description=(
            "How long an ended LLM stream waits for its spend record to be stored before letting "
            "go (services/llm_usage_recording.py, #16924). The record keeps running in the "
            "background past this, and a failure is still logged; only the wait is bounded."
        ),
        component="backend",
        range=(0.1, 120.0),
    )
)
