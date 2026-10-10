# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The one resolver for the active embedding model (#18143).

The generator (`services.npu_client`) and every embedding-cache key must name the same
model, or a vector made by model A is served under model B's key. Both ask this function,
so a change to ``AUTOBOT_EMBEDDING_MODEL`` moves them together.
"""

from __future__ import annotations


def active_embedding_model() -> str:
    """Return the configured embedding model, read at call time (never cached)."""
    from autobot_shared.ssot_config import config

    return config.llm.embedding_model
