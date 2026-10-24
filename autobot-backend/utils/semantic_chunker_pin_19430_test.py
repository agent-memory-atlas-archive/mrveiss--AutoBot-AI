# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""An unpinned sentence model fails when the chunker is built, not silently in a load fallback (#19430)."""

from __future__ import annotations

import pytest

from utils.semantic_chunker import AutoBotSemanticChunker
from utils.semantic_chunker_base import SENTENCE_EMBEDDING_MODEL


def test_the_pinned_default_constructs_without_loading() -> None:
    chunker = AutoBotSemanticChunker()
    assert chunker.embedding_model_name == SENTENCE_EMBEDDING_MODEL
    assert chunker._embedding_model is None


def test_an_unpinned_name_is_refused_at_construction() -> None:
    # The short name resolves on the Hub, but it is not a pinned repo id: before #19430 a load
    # failure here fell back to downloading a different model with no error.
    with pytest.raises(KeyError):
        AutoBotSemanticChunker(embedding_model="all-MiniLM-L6-v2")
