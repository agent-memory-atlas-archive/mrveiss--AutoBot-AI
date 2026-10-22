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
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Tuple

from autobot_shared.logging_manager import get_logger

logger = get_logger(__name__)

#: (kind) -> (metadata ``type`` the librarian stores it under, metadata field holding the name).
#: Legacy facts (stored before stable ids existed) are found by these two fields.
_KIND_METADATA = {
    "tool": ("tool_documentation", "tool_name"),
    "workflow": ("workflow_documentation", "workflow_name"),
    "procedure": ("system_documentation", "title"),
}

_FACT_ID_NAMESPACE = uuid.UUID("5f0c1d6e-0b7a-5c1e-9a55-18577a1b2c3d")


def system_fact_id(kind: str, name: str) -> str:
    """Stable fact id for a system-knowledge item (same kind+name -> same id, every run)."""
    return str(uuid.uuid5(_FACT_ID_NAMESPACE, f"{kind}:{name}"))


SYSTEM_SOURCE = "system_knowledge"
LINK_FIELD = "system_fact_id"


@dataclass(frozen=True)
class SystemFactRef:
    """Which fact a system-knowledge item is stored in, and whether the import owns it.

    ``legacy`` is a fact found by (kind, name) that the import did not create (typically stored by
    web research under a random id): it is linked, never rewritten.
    """

    fact_id: str
    stable_id: str
    legacy: bool = False


async def store_or_update_fact(knowledge_base: Any, content: str, metadata: Dict[str, Any], ref: Any = None):
    """Store a fact. With a :class:`SystemFactRef` an existing fact is updated, never duplicated.

    - no ``ref``: behaviour unchanged (web research keeps its random ids and its source);
    - owned fact (stable id, or tagged ``source=system_knowledge``): body and metadata update, and
      the fact is tagged ``source=system_knowledge``;
    - legacy fact: only the link to the stable id is added. Its text and its ``source`` stay.
    """
    if ref is None:
        return await knowledge_base.store_fact(content, metadata=metadata)
    if ref.legacy:
        return await knowledge_base.update_fact(ref.fact_id, metadata={LINK_FIELD: ref.stable_id})
    metadata = {**metadata, "source": SYSTEM_SOURCE, LINK_FIELD: ref.stable_id}
    if await asyncio.to_thread(knowledge_base.get_fact, ref.fact_id):
        return await knowledge_base.update_fact(ref.fact_id, content=content, metadata=metadata)
    return await knowledge_base.store_fact(content, metadata=metadata, fact_id=ref.fact_id)


async def _durable_batches() -> AsyncIterator[List[Dict[str, Any]]]:
    """Batches of the durable facts whose metadata ``type`` is one the system import writes.

    One SQL query, filtered in Postgres (``metadata_json->>'type'``): there is no index on that
    JSON, so it is a table scan inside the database, but only matching rows cross the wire.
    """
    from knowledge import fact_store  # lazy: pulls in the DB layer
    from models.knowledge_fact import KnowledgeFact

    types = [kind_type for kind_type, _ in _KIND_METADATA.values()]
    where = (KnowledgeFact.metadata_json["type"].astext.in_(types),)
    async for batch in fact_store.iter_facts(where=where):
        yield batch


class SystemFactIds:
    """Resolves the fact id a system-knowledge item is stored under (#18577).

    ``load()`` reads the existing system facts ONCE per import run and indexes them by
    (kind, name); ``of()`` then returns the existing fact's id (adopt it in place, whatever its
    id) or, when none exists, the stable uuid5 id. With several legacy facts for one item the
    oldest wins and the rest are left untouched.
    """

    def __init__(self, batches: Callable[[], AsyncIterator[List[Dict[str, Any]]]] = _durable_batches) -> None:
        self._batches = batches
        self._existing: Dict[Tuple[str, str], Tuple[str, bool]] = {}

    async def load(self) -> None:
        by_type = {kind_type: (kind, field) for kind, (kind_type, field) in _KIND_METADATA.items()}
        existing: Dict[Tuple[str, str], Tuple[str, bool]] = {}
        async for batch in self._batches():
            for fact in batch:
                metadata = fact["metadata"]
                kind, field = by_type[metadata["type"]]
                name = metadata.get(field)
                if not name:
                    continue
                owned = fact["fact_id"] == system_fact_id(kind, name) or metadata.get("source") == SYSTEM_SOURCE
                held = existing.get((kind, name))
                if held is None or (owned and not held[1]):  # oldest wins; an owned fact beats a legacy one
                    existing[(kind, name)] = (fact["fact_id"], owned)
        self._existing = existing
        logger.info("Indexed %d existing system-knowledge facts", len(existing))

    def of(self, kind: str, name: str) -> SystemFactRef:
        stable = system_fact_id(kind, name)
        fact_id, owned = self._existing.get((kind, name), (stable, True))
        return SystemFactRef(fact_id=fact_id, stable_id=stable, legacy=not owned)
