# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Pins each agent's resolved default model to its canonical tier (#15502).

`complete.yaml` no longer carries `agents.<id>.model`; `DEFAULT_AGENT_CONFIGS` resolves every
agent to a tier constant that is read from the source-of-truth tier map, never a model literal.
"""

import pytest

import autobot_shared.ssot_config as ssot
from api.agent_config import DEFAULT_AGENT_CONFIGS

#: agent id -> the canonical tier constant its default model must equal.
AGENT_TIER = {
    "orchestrator": "ROUTING_MODEL",
    "chat": "QUALITY_MODEL",
    "classification": "CLASSIFICATION_MODEL",
    "kb_librarian": "LIGHT_PROCESSING_MODEL",
    "research": "QUALITY_MODEL",
    "system_commands": "SYSTEM_MODEL",
    "security_scanner": "SYSTEM_MODEL",
    "code_analysis": "QUALITY_MODEL",
}


@pytest.mark.parametrize(("agent_id", "tier"), sorted(AGENT_TIER.items()))
def test_agent_default_model_resolves_from_its_tier(agent_id: str, tier: str) -> None:
    assert DEFAULT_AGENT_CONFIGS[agent_id]["default_model"] == getattr(ssot, tier)


def test_no_agent_default_is_outside_the_tier_map() -> None:
    tiers = {
        getattr(ssot, n)
        for n in (
            "ROUTING_MODEL",
            "CLASSIFICATION_MODEL",
            "LIGHT_PROCESSING_MODEL",
            "INSTRUCTION_MODEL",
            "SYSTEM_MODEL",
            "QUALITY_MODEL",
        )
    }
    stray = {a: c["default_model"] for a, c in DEFAULT_AGENT_CONFIGS.items() if c["default_model"] not in tiers}
    assert stray == {}, f"agent defaults naming a model outside the tier map: {stray}"
