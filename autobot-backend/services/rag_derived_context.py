# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""
RAGService's derived-content context legs, scoped to the caller (#4678, #18944).

The KB synthesis summaries and the analyzer lessons are read straight from ChromaDB collections, outside
the facts store. Each hit goes through ``filter_for_principal`` -- the one filter -- before it can join a
context, so a derived document the caller may not read never reaches a response.
"""

from typing import List

from autobot_shared.logging_manager import get_llm_logger
from knowledge.search_filters import KbPrincipal, filter_for_principal

logger = get_llm_logger("rag_service")


class RAGDerivedContextMixin:
    """Context legs mixed into ``RAGService``; relies on its ``_ownership_manager``."""

    async def _visible_docs(self, collection, query: str, principal: KbPrincipal) -> List[str]:
        """Top documents of a derived-content *collection* that *principal* may read (#18944).

        The one filter: ``filter_for_principal`` over each hit's stored metadata. A hit with no
        ownership or visibility metadata reads as private, so it never reaches a signed-in caller.
        """
        res = await collection.query(query_texts=[query], n_results=2, include=["documents", "metadatas"])
        if not (res and res.get("ids") and res["ids"][0]):
            return []
        ids, docs = res["ids"][0], res.get("documents", [[]])[0]
        metas = list((res.get("metadatas") or [[]])[0])
        metas += [None] * (len(ids) - len(metas))
        hits = [{"id": i, "metadata": m or {}, "doc": d} for i, d, m in zip(ids, docs, metas) if d]
        kept = await filter_for_principal(hits, principal, self._ownership_manager())
        return [h["doc"] for h in kept]

    async def _get_analyzer_lessons_context(self, query: str, principal: KbPrincipal) -> str:
        """Query the ``autobot_lessons`` ChromaDB collection for supplemental context.

        Issue #4678: Injects AnalyzerService-distilled lessons as low-weight
        supplemental context after primary synthesis summaries.  Requires
        ``RAGConfig.enable_analyzer_lessons`` to be True (checked by caller).

        Returns an empty string on any error so the main context path is never
        interrupted.
        """
        try:
            from knowledge.backends import get_async_default_client

            client = await get_async_default_client()
            collection = await client.get_or_create_collection(name="autobot_lessons")
            relevant = await self._visible_docs(collection, query, principal)
            if not relevant:
                return ""
            return "Analyzer lessons:\n" + "\n".join(f"- {d}" for d in relevant)
        except Exception as exc:
            logger.debug("Analyzer lessons fetch failed (non-fatal): %s", exc)
            return ""
