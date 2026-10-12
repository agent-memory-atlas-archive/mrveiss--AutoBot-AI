# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""add_document chunks (#17830) and keeps what it stored when the budget runs out (#17986)."""

import asyncio
from unittest.mock import PropertyMock, patch

import pytest

import knowledge.document_ingest as ingest_module
from autobot_shared.doc_chunking import estimate_tokens
from autobot_shared.status_enums import OperationOutcome
from knowledge.document_ingest import chunk_fact_id, read_ingest_record
from knowledge.documents import DocumentsMixin
from utils.knowledge_base_timeouts import kb_timeouts

CHUNK_TOKENS = 100


class _FakeRedis:
    def __init__(self):
        self.hashes, self.facts = {}, set()

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})

    def expire(self, key, ttl):
        return True

    def exists(self, key):
        return key in self.facts


class _FakeKB(DocumentsMixin):
    """A DocumentsMixin host whose storing a chunk costs ``delay`` seconds."""

    def __init__(self, delay=0.0):
        self.delay, self.redis_client = delay, _FakeRedis()
        self.stored, self.revectorized = {}, []

    async def _add_document_internal(self, content, metadata=None, doc_id=None):
        await asyncio.sleep(self.delay)
        self.stored[doc_id] = (content, dict(metadata or {}))
        self.redis_client.facts.add("fact:%s" % doc_id)
        return {"status": "success", "fact_id": doc_id}

    async def vectorize_existing_fact(self, fact_id):
        self.revectorized.append(fact_id)
        return {"status": "success", "fact_id": fact_id}


@pytest.fixture(autouse=True)
def _small_chunks():
    with patch.object(ingest_module, "KB_CHUNK_MAX_TOKENS", CHUNK_TOKENS):
        yield


def _budget(seconds):
    return patch.object(type(kb_timeouts), "document_add", new_callable=PropertyMock, return_value=seconds)


def _transcript(sentences=400):
    """One unbroken block, no paragraph breaks -- the shape of a meeting transcript."""
    return " ".join("Speaker said point number %d about the roadmap." % i for i in range(sentences))


def _pdf_text(pages=12):
    return "\n\n".join(
        "\n\n".join("Page %d paragraph %d. " % (p, q) + "text " * 60 for q in range(3)) for p in range(pages)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [_transcript(), _pdf_text()], ids=["transcript", "pdf-text"])
async def test_long_document_is_stored_as_several_chunks_sharing_a_document_id(text):
    kb = _FakeKB()
    result = await kb.add_document(text, {"title": "t"}, doc_id="doc-1")
    assert result["status"] == "success"
    assert len(kb.stored) > 1 and result["chunks_stored"] == result["chunks_total"] == len(kb.stored)
    assert chunk_fact_id("doc-1", 0) in kb.stored and result["fact_id"] == "doc-1"
    for idx, (chunk, meta) in enumerate(kb.stored.values()):
        assert estimate_tokens(chunk) <= CHUNK_TOKENS * 1.2
        assert meta["document_id"] == "doc-1" and meta["chunk_index"] == idx and meta["chunk_count"] == len(kb.stored)


@pytest.mark.asyncio
async def test_short_document_is_stored_whole_and_unchanged():
    kb = _FakeKB()
    result = await kb.add_document("short note", {"title": "t"}, doc_id="d")
    assert result == {"status": "success", "fact_id": "d"}
    assert kb.stored["d"] == ("short note", {"title": "t"})


@pytest.mark.asyncio
async def test_budget_overrun_keeps_stored_chunks_and_reports_partial():
    kb = _FakeKB(delay=0.03)
    with _budget(0.1):
        result = await kb.add_document(_transcript(), doc_id="doc-2")
    assert result["status"] == OperationOutcome.PARTIAL.value
    assert 0 < result["chunks_stored"] < result["chunks_total"] and len(kb.stored) == result["chunks_stored"]
    record = await read_ingest_record(kb, "doc-2")
    assert record["status"] == "partial" and int(record["chunks_stored"]) == len(kb.stored)


@pytest.mark.asyncio
async def test_partial_complete_and_failed_are_three_distinct_outcomes():
    complete = await _FakeKB().add_document(_transcript(), doc_id="a")
    with _budget(0.1):
        partial = await _FakeKB(delay=0.03).add_document(_transcript(), doc_id="b")
    with _budget(0.001):
        failed = await _FakeKB(delay=0.05).add_document(_transcript(), doc_id="c")
    assert [r["status"] for r in (complete, partial, failed)] == ["success", "partial", "timeout"]
    assert failed["chunks_stored"] == 0


@pytest.mark.asyncio
async def test_retry_resumes_after_the_last_stored_chunk_instead_of_restarting():
    kb, text = _FakeKB(delay=0.03), _transcript()
    with _budget(0.1):
        first = await kb.add_document(text, doc_id="doc-3")
    done = set(kb.stored)
    kb.delay, stores = 0.0, []
    original = kb._add_document_internal

    async def spy(content, metadata=None, doc_id=None):
        stores.append(doc_id)
        return await original(content, metadata, doc_id)

    kb._add_document_internal = spy
    with _budget(5.0):
        second = await kb.add_document(text, doc_id="doc-3")
    assert second["status"] == "success" and second["chunks_stored"] == first["chunks_total"]
    assert not done & set(stores) - {chunk_fact_id("doc-3", len(done))}, "already-stored chunks were stored again"
    assert len(kb.stored) == first["chunks_total"]


@pytest.mark.asyncio
async def test_changed_content_under_the_same_id_restarts_instead_of_resuming():
    kb = _FakeKB(delay=0.03)
    with _budget(0.1):
        await kb.add_document(_transcript(), doc_id="doc-4")
    kb.delay = 0.0
    result = await kb.add_document(_transcript(300), doc_id="doc-4")
    assert result["status"] == "success" and result["chunks_stored"] == result["chunks_total"]
