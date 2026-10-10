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


class EmbeddingUnavailableError(RuntimeError):
    """The active embedding model could not produce a vector.

    Raised instead of embedding with a different model: vectors from another model are not
    comparable with the stored ones (#18185).
    """


def raise_if_embedding_refused(metrics: object) -> None:
    """Raise EmbeddingUnavailableError if a search's metrics record an embedding refusal (#18185).

    A refused search returns ``([], metrics)`` with ``metrics.error`` set; a caller that only
    reads the results would report an outage as "no matches".
    """
    reason = getattr(metrics, "error", None)
    if reason:
        raise EmbeddingUnavailableError(reason)


def active_embedding_model() -> str:
    """Return the configured embedding model, read at call time (never cached)."""
    from autobot_shared.ssot_config import config

    return config.llm.embedding_model
