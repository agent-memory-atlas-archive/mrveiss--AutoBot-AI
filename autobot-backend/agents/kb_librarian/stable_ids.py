# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Stable fact ids and the idempotent store for system knowledge (#18577).

The librarian's document bodies embed a timestamp, so the KB's content-hash dedup never matches a
re-import. One id per (kind, name) makes a repeat import update the fact instead of adding a copy.
"""

import asyncio
import uuid
from typing import Any, Dict

_FACT_ID_NAMESPACE = uuid.UUID("5f0c1d6e-0b7a-5c1e-9a55-18577a1b2c3d")


def system_fact_id(kind: str, name: str) -> str:
    """Stable fact id for a system-knowledge item (same kind+name -> same id, every run)."""
    return str(uuid.uuid5(_FACT_ID_NAMESPACE, f"{kind}:{name}"))


async def store_or_update_fact(knowledge_base: Any, content: str, metadata: Dict[str, Any], fact_id: str | None = None):
    """Store a fact; with a stable ``fact_id`` an existing fact is updated, never duplicated.

    Without ``fact_id`` behaviour is unchanged (web research keeps its random ids).
    """
    if fact_id is None:
        return await knowledge_base.store_fact(content, metadata=metadata)
    if await asyncio.to_thread(knowledge_base.get_fact, fact_id):
        return await knowledge_base.update_fact(fact_id, content=content, metadata=metadata)
    return await knowledge_base.store_fact(content, metadata=metadata, fact_id=fact_id)
