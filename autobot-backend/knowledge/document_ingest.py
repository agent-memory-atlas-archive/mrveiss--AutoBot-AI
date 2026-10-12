# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Chunked, budgeted, resumable document ingest for ``add_document`` (#17830, #17986).

A document is split by ``autobot_shared.doc_chunking`` into chunks that are stored
as ordinary facts sharing a ``document_id``. Chunk 0 keeps the document id as its
fact id (so existing callers' ``fact_id`` stays addressable); later chunks use
``<doc_id>:chunk:<n>``. A per-document record in Redis carries the ingest status:

(``autobot_shared.status_enums.OperationOutcome``; also the result's ``status``)

* ``success``  -- every chunk stored
* ``partial``  -- the time budget ran out after at least one chunk was stored
* ``timeout``  -- the budget ran out with nothing stored

A retry with the same ``doc_id`` and the same content resumes after the last
stored chunk instead of restarting.
"""

import asyncio
import hashlib
import os
import time
from typing import Any, Dict, List

from autobot_shared.doc_chunking import chunk_plain_text
from autobot_shared.logging_manager import get_logger
from autobot_shared.status_enums import OperationOutcome

logger = get_logger(__name__)

INGEST_RECORD_PREFIX = "kb:ingest:"
# Token budget per stored chunk (~4 chars/token) and how long a per-document ingest record is kept for resume.
KB_CHUNK_MAX_TOKENS = int(os.getenv("AUTOBOT_KB_CHUNK_MAX_TOKENS", "800"))
KB_INGEST_RECORD_TTL_SECONDS = int(os.getenv("AUTOBOT_KB_INGEST_RECORD_TTL_SECONDS", str(30 * 24 * 3600)))


def chunk_fact_id(doc_id: str, index: int) -> str:
    """Deterministic fact id of chunk *index*; chunk 0 is the document id itself."""
    return doc_id if index == 0 else "%s:chunk:%d" % (doc_id, index)


def _chunk_metadata(metadata: Dict[str, Any], doc_id: str, index: int, total: int) -> Dict[str, Any]:
    chunk_meta = dict(metadata)
    if total > 1:
        chunk_meta.update({"document_id": doc_id, "chunk_index": index, "chunk_count": total})
    return chunk_meta


def _record_key(doc_id: str) -> str:
    return INGEST_RECORD_PREFIX + doc_id


async def read_ingest_record(kb, doc_id: str) -> Dict[str, str]:
    """Return the ingest record for *doc_id* (empty dict if none)."""
    raw = await asyncio.to_thread(kb.redis_client.hgetall, _record_key(doc_id))
    return {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in (raw or {}).items()
    }


async def _write_record(kb, doc_id: str, status: OperationOutcome, stored: int, total: int, digest: str) -> None:
    key = _record_key(doc_id)
    mapping = {
        "status": status.value,
        "chunks_stored": stored,
        "chunks_total": total,
        "content_digest": digest,
        "updated_at": int(time.time()),
    }
    await asyncio.to_thread(kb.redis_client.hset, key, mapping=mapping)
    await asyncio.to_thread(kb.redis_client.expire, key, KB_INGEST_RECORD_TTL_SECONDS)


async def _resume_point(kb, doc_id: str, digest: str) -> tuple[int, bool]:
    """(chunks already stored, whether a record for this exact content exists)."""
    record = await read_ingest_record(kb, doc_id)
    if record.get("content_digest") != digest:
        return 0, False
    return int(record.get("chunks_stored", 0)), True


async def _store_chunk(kb, text: str, metadata: Dict[str, Any], fact_id: str, interrupted: bool) -> Dict[str, Any]:
    """Store one chunk. The chunk a prior attempt was interrupted on may already be half-written
    (durable row, vector missing) -- that one is re-vectorized rather than deduplicated away."""
    if interrupted and await asyncio.to_thread(kb.redis_client.exists, "fact:%s" % fact_id):
        return await kb.vectorize_existing_fact(fact_id)
    return await kb._add_document_internal(text, metadata, fact_id)


def _outcome(status: OperationOutcome, doc_id: str, stored: int, total: int, first: Dict[str, Any]) -> Dict[str, Any]:
    """Build the caller-facing result; ``status`` keeps the vocabulary pre-chunking callers expect."""
    result = dict(first)
    result.update(
        {
            "fact_id": doc_id if stored else None,
            "status": status.value,
            "document_id": doc_id,
            "chunks_stored": stored,
            "chunks_total": total,
        }
    )
    return result


async def _ingest_single(kb, text: str, metadata: Dict[str, Any], doc_id: str, budget: float) -> Dict[str, Any]:
    """A document within one chunk: stored as before, all-or-nothing."""
    try:
        return await asyncio.wait_for(kb._add_document_internal(text, metadata, doc_id), timeout=budget)
    except asyncio.TimeoutError:
        logger.warning("Document addition timed out")
        return {"status": "timeout", "message": "Document addition timed out"}


async def ingest_document(kb, content: str, metadata: Dict[str, Any], doc_id: str, budget: float) -> Dict[str, Any]:
    """Chunk *content* and store it within *budget* seconds, keeping what was stored on timeout."""
    chunks: List[str] = chunk_plain_text(content, KB_CHUNK_MAX_TOKENS)
    total = len(chunks)
    if total == 1:
        return await _ingest_single(kb, content, metadata, doc_id, budget)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    stored, resumed = await _resume_point(kb, doc_id, digest)
    deadline = time.monotonic() + budget
    first: Dict[str, Any] = {}
    status = OperationOutcome.SUCCESS
    for index in range(stored, total):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            chunk_meta = _chunk_metadata(metadata, doc_id, index, total)
            res = await asyncio.wait_for(
                _store_chunk(kb, chunks[index], chunk_meta, chunk_fact_id(doc_id, index), resumed and index == stored),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            logger.warning("Document %s ingest budget exhausted at chunk %d/%d", doc_id, index, total)
            status = OperationOutcome.PARTIAL if stored else OperationOutcome.TIMEOUT
            break
        if res.get("status") not in ("success", "duplicate"):
            if not stored:
                return res
            status = OperationOutcome.PARTIAL
            break
        first = first or res
        stored = index + 1
        await _write_record(kb, doc_id, OperationOutcome.PARTIAL, stored, total, digest)
    await _write_record(kb, doc_id, status, stored, total, digest)
    return _outcome(status, doc_id, stored, total, first)
