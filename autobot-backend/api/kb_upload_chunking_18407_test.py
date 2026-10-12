# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Upload, /facts and /url store through add_document, so long input is chunked (#18407)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import knowledge.document_ingest as ingest_module
from knowledge.documents import DocumentsMixin

LONG_TEXT = "\n\n".join("Section %d. " % i + "lorem ipsum dolor sit amet " * 40 for i in range(30))


class _KB(DocumentsMixin):
    def __init__(self):
        self.redis_client = MagicMock(hgetall=MagicMock(return_value={}))
        self.stored = {}

    async def _add_document_internal(self, content, metadata=None, doc_id=None):
        self.stored[doc_id] = (content, dict(metadata or {}))
        return {"status": "success", "fact_id": doc_id}


@pytest.fixture(autouse=True)
def _small_chunks():
    with patch.object(ingest_module, "KB_CHUNK_MAX_TOKENS", 200):
        yield


def _patched(mod, kb):
    return (
        patch.object(mod, "get_or_create_knowledge_base", AsyncMock(return_value=kb)),
        patch.object(mod, "get_auth_middleware", MagicMock()),
        patch.object(mod, "audit_record", MagicMock()),
    )


def _assert_chunked_under_one_document(kb, response_id):
    assert len(kb.stored) > 1, "a long input was stored as a single fact"
    assert {m["document_id"] for _c, m in kb.stored.values()} == {response_id}
    assert response_id in kb.stored


@pytest.mark.asyncio
async def test_a_long_upload_is_stored_as_several_chunks_under_the_returned_document_id():
    from api import knowledge as mod

    upload = SimpleNamespace(filename="notes.txt", read=AsyncMock(return_value=LONG_TEXT.encode("utf-8")))
    req = MagicMock()
    req.form = AsyncMock(return_value={"file": upload, "title": "Notes"})
    kb = _KB()
    p1, p2, p3 = _patched(mod, kb)
    with p1, p2, p3:
        response = await mod.upload_file_to_knowledge(admin_check=True, req=req)
    _assert_chunked_under_one_document(kb, response.document_id)


@pytest.mark.asyncio
async def test_a_long_pdf_upload_chunks_carry_only_their_own_pages():
    from api import knowledge as mod

    pages = ["Page %d marker. " % n + " ".join("w%d_%d" % (n, i) for i in range(400)) for n in range(1, 7)]
    spans, text = [], ""
    for n, page in enumerate(pages, 1):
        spans.append({"page": n, "start": len(text), "end": len(text) + len(page)})
        text += page + "\n\n"
    upload = SimpleNamespace(filename="doc.txt", read=AsyncMock(return_value=b"x"))
    req = MagicMock()
    req.form = AsyncMock(return_value={"file": upload, "title": "Doc"})
    kb = _KB()
    extracted = MagicMock()
    p1, p2, p3 = _patched(mod, kb)
    with (
        p1,
        p2,
        p3,
        patch.object(mod, "extract_within_deadline", AsyncMock(return_value=(text.rstrip(), extracted))),
        patch.object(mod, "_has_usable_content", return_value=True),
        patch.object(mod, "_page_provenance_metadata", return_value={"page_count": 6, "page_spans": spans}),
    ):
        response = await mod.upload_file_to_knowledge(admin_check=True, req=req)
    _assert_chunked_under_one_document(kb, response.document_id)
    for chunk, meta in kb.stored.values():
        named = {int(n) for n in __import__("re").findall(r"Page (\d) marker", chunk)}
        assert named <= {s["page"] for s in meta["page_spans"]} <= set(range(1, 7))
        assert all(s["end"] <= len(chunk) for s in meta["page_spans"])
        assert len({s["page"] for s in meta["page_spans"]}) < 6, "chunk claims every page"


@pytest.mark.asyncio
async def test_add_fact_and_url_content_are_chunked_through_the_same_helper():
    from api import knowledge as mod

    for metadata in ({"source": "s"}, {"type": "url", "source": "https://example.invalid"}):
        kb = _KB()
        doc_id = await mod._store_fact_in_kb(kb, LONG_TEXT, dict(metadata))
        _assert_chunked_under_one_document(kb, doc_id)
