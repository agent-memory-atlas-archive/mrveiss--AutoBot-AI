# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Atomic get-or-create for graph entities, keyed by name (#18417).

The identity is a name-index key (``memory:entity_name:<sha256>``) pointing at the
entity id. Claiming the name and writing the entity document happen in ONE Lua
script, so no caller can observe a claim without its document, and a claim whose
document is gone (a deleted or crashed entity) is repaired inside the same atomic
step. The index is a dedup index, not a work claim.

Every read here RAISES on a Redis error. An unreadable lookup is never 'absent'.
"""

import hashlib
import json
import uuid
from typing import Any, Dict, List

from autobot_shared.logging_manager import get_logger

from .core import AutoBotMemoryGraphCore

logger = get_logger(__name__)

ENTITY_KEY_PREFIX = "memory:entity:"

#: KEYS[1] name index, KEYS[2] new entity document; ARGV[1] new id, ARGV[2] document
#: JSON, ARGV[3] entity key prefix. Returns the id that owns the name afterwards.
CLAIM_AND_WRITE_LUA = """
local existing = redis.call('GET', KEYS[1])
-- ARGV[3] .. existing builds a key inside the script: not Redis-Cluster-safe; no cluster is used.
if existing and redis.call('EXISTS', ARGV[3] .. existing) == 1 then
    return existing
end
redis.call('SET', KEYS[1], ARGV[1])
redis.call('JSON.SET', KEYS[2], '$', ARGV[2])
return ARGV[1]
"""


#: KEYS[1] name index; ARGV[1] the id the caller believes owns it. Deletes only on a match,
#: atomically, so a re-claim landing after a read cannot lose its live claim.
RELEASE_IF_OWNER_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def name_index_key(name: str) -> str:
    """Redis key of the name -> entity-id index (case and edge whitespace are not identity)."""
    digest = hashlib.sha256(name.strip().casefold().encode("utf-8")).hexdigest()
    return f"memory:entity_name:{digest}"


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


async def indexed_entity(graph: AutoBotMemoryGraphCore, name: str) -> Dict[str, Any] | None:
    """The entity the name index points at, or None. Redis errors raise."""
    claimed = await graph.redis_client.get(name_index_key(name))
    if not claimed:
        return None
    return await graph.redis_client.json().get(f"{ENTITY_KEY_PREFIX}{_text(claimed)}")


async def adopt_legacy_entity(graph: AutoBotMemoryGraphCore, name: str) -> Dict[str, Any] | None:
    """Index an exact-name entity that predates the index. The lookup is strict: it raises."""
    legacy = await graph.get_entity(entity_name=name, strict=True)
    if not legacy or legacy.get("_resolution") != "exact":
        return None
    await graph.redis_client.set(name_index_key(name), legacy["id"], nx=True)
    return await indexed_entity(graph, name) or legacy


async def claim_and_write(graph: AutoBotMemoryGraphCore, name: str, entity: Dict[str, Any]) -> str:
    """Atomically claim ``name`` and write ``entity``; returns the id that owns the name."""
    owner = await graph.redis_client.eval(
        CLAIM_AND_WRITE_LUA,
        2,
        name_index_key(name),
        f"{ENTITY_KEY_PREFIX}{entity['id']}",
        entity["id"],
        json.dumps(entity),
        ENTITY_KEY_PREFIX,
    )
    return _text(owner)


async def get_or_create(
    graph: AutoBotMemoryGraphCore,
    entity_type: str,
    name: str,
    observations: List[str],
    metadata: Dict[str, Any] | None,
    tags: List[str] | None,
) -> tuple[Dict[str, Any], bool]:
    """Return ``(entity, created)``; see ``AutoBotMemoryGraph.get_or_create_entity``."""
    graph._validate_entity_inputs(entity_type, name)
    existing = await indexed_entity(graph, name) or await adopt_legacy_entity(graph, name)
    if existing:
        return existing, False
    entity_id = str(uuid.uuid4())
    entity_metadata = graph._prepare_entity_metadata(metadata, tags)
    entity = graph._build_entity_document(entity_id, entity_type, name, observations, entity_metadata)
    owner = await claim_and_write(graph, name, entity)
    if owner != entity_id:
        winner = await graph.redis_client.json().get(f"{ENTITY_KEY_PREFIX}{owner}")
        if winner is None:
            raise RuntimeError(f"Entity {name!r} changed while being created; retry")
        return winner, False
    if graph.knowledge_base:
        await graph._generate_entity_embedding(entity_id, entity)
    logger.info("Created entity: %s (%s) with ID %s", name, entity_type, entity_id[:8] + "...")
    return entity, True


async def release_name(graph: AutoBotMemoryGraphCore, name: str, entity_id: str) -> None:
    """Drop the name index entry, but only if it still points at ``entity_id`` (atomic)."""
    await graph.redis_client.eval(RELEASE_IF_OWNER_LUA, 1, name_index_key(name), entity_id)


async def strict_search_by_name(graph: AutoBotMemoryGraphCore, name: str, limit: int) -> List[Dict[str, Any]]:
    """Name candidates for ``get_entity(strict=True)``. Redis errors raise, never read as 'none'.

    RediSearch first; if the index is unavailable, a full key scan. Both paths raise when
    Redis itself is unreachable, so only a genuine absence returns an empty list.
    """
    from .queries import _is_entity_valid

    try:
        hits = await graph._execute_redis_search(graph._build_redis_search_query(name, None, None, None), limit)
        return [e for e in hits if _is_entity_valid(e)]
    except Exception as search_error:
        logger.warning("RediSearch unavailable for strict lookup, scanning keys: %s", search_error)
    folded = name.strip().casefold()
    found: List[Dict[str, Any]] = []
    async for key in graph.redis_client.scan_iter(match=f"{ENTITY_KEY_PREFIX}*"):
        doc = await graph.redis_client.json().get(key)
        if doc and str(doc.get("name", "")).strip().casefold() == folded and _is_entity_valid(doc):
            found.append(doc)
    return found
