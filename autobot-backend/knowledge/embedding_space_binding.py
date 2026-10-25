# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Keep a knowledge-base process bound to the active embedding space (#19379).

The KB's collection is several objects, not one name: the raw collection, the LlamaIndex
vector store over it, the write buffer whose flush closes over that store, the lazy vector
index, and LlamaIndex's embed model. A switch writes one record (model + collection +
generation, ``autobot_shared.embedding_model``); each process -- every uvicorn worker and
Celery -- compares that record's generation with the one it bound and rebinds all of them
together. Queued writes are drained into the old collection first; facts written during a
build are replayed into the new one by the switch itself.
"""

from __future__ import annotations

import asyncio
import time

from autobot_shared.embedding_model import EmbeddingSpace, active_embedding_space
from autobot_shared.env_utils import env_float_clamped
from autobot_shared.logging_manager import get_logger

logger = get_logger(__name__)

#: How often a process re-reads the record to notice a switch made elsewhere.
SPACE_CHECK_INTERVAL_S = env_float_clamped("AUTOBOT_EMBEDDING_SPACE_CHECK_SECONDS", 5.0, 0.0, 300.0)

_REBIND_LOCK = asyncio.Lock()


def initial_collection(kb, configured: str) -> str:
    """The collection a KB binds at init: a switched space's, else the configured one."""
    space = active_embedding_space()
    kb._bound_generation = space.generation if space is not None and space.collection else 0
    kb._space_checked_at = time.monotonic()
    return space.collection if space is not None and space.collection else configured


async def ensure_current_space(kb) -> bool:
    """Rebind *kb* if another process cut over to a newer space; True when it rebound."""
    now = time.monotonic()
    if now - getattr(kb, "_space_checked_at", 0.0) < SPACE_CHECK_INTERVAL_S:
        return False
    kb._space_checked_at = now
    space = active_embedding_space()
    if space is None or not space.collection or space.generation <= getattr(kb, "_bound_generation", 0):
        return False
    await rebind(kb, space)
    return True


async def rebind(kb, space: EmbeddingSpace) -> None:
    """Point *kb* at *space*'s collection and model, draining queued writes into the old one."""
    async with _REBIND_LOCK:
        if space.generation <= getattr(kb, "_bound_generation", 0):
            return
        old_buffer = getattr(kb, "_write_buffer", None)
        if old_buffer is not None:
            await old_buffer.stop()  # flushes what is queued into the collection it was made for
        from knowledge.backends import get_default_client

        client = await asyncio.to_thread(
            get_default_client, db_path=str(kb.chromadb_path), allow_reset=False, anonymized_telemetry=False
        )
        kb.chromadb_collection = space.collection
        await kb._create_chroma_collection(client, kb._build_hnsw_metadata())
        kb.vector_index = None  # rebuilt lazily over the new store
        _rebind_embed_model(kb, space.model)
        if kb._write_buffer is not None and old_buffer is not None:
            await kb._write_buffer.start()
        kb._bound_generation = space.generation
        logger.warning(
            "Knowledge base rebound to embedding space %s in %s (generation %d, #19379)",
            space.model,
            space.collection,
            space.generation,
        )


def _rebind_embed_model(kb, model: str) -> None:
    from autobot_shared.ssot_config import config as ssot_config

    provider = ssot_config.llm.llamaindex_embedding_provider.lower()
    endpoint = ssot_config.llm.llamaindex_embedding_endpoint
    kb.embedding_dimensions = kb._configure_embedding_provider(provider, model, endpoint, ssot_config)
    kb.embedding_model_name = model
