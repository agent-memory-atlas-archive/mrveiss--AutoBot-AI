# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Switch the embedding model: rebuild into a new collection in the background, then cut over (#19379).

- **Start:** a background job re-embeds every fact from the system of record
  (``fact_store.iter_facts``, PostgreSQL) with the target model into a NEW collection, stamped
  with its embedding provenance. The active collection is not touched.
- **During the build:** search and writes keep using the active collection. Every fact written,
  updated or deleted meanwhile is noted (``note_write``, called by ``fact_store``) and replayed
  into the new collection before cutover, because ``iter_facts`` deliberately stops at the
  facts that existed when it started.
- **Cutover**, only on a complete build with zero failures (owner decision, 2026-10-25): the
  record (model + collection) is written once (``set_embedding_model_override`` -- this module is
  its only caller) and this process rebinds; others follow on their next check
  (``embedding_space_binding``).
- **Failure or cancel:** the active model and collection stay as they were, and the state says
  why. The new collection is left for the deletion proposal (#19380).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from autobot_shared.async_compat import fire_and_forget
from autobot_shared.embedding_model import active_embedding_model, is_embedding_model_id, set_embedding_model_override
from autobot_shared.env_utils import env_int_clamped
from autobot_shared.logging_manager import get_logger

logger = get_logger(__name__)

STATE_KEY = "autobot:kb:embedding_switch"
PENDING_KEY = "autobot:kb:embedding_switch:pending"
#: Facts embedded per round trip to the embedding provider.
SWITCH_BATCH_SIZE = env_int_clamped("AUTOBOT_EMBEDDING_SWITCH_BATCH_SIZE", 64, 1, 1000)
#: Failed fact ids kept in the state for the operator; the count is always exact.
_FAILED_IDS_SHOWN = 50
#: While building, and while settling after cutover, every write is noted for replay. Settling
#: covers processes that still write to the old collection until they notice the switch.
_NOTING_STATES = ("building", "settling")


class SwitchRefused(ValueError):
    """A switch request that cannot start (bad model, one already running, already active)."""


def target_collection(base: str, model: str) -> str:
    """The new collection's name: the KB's base name plus the model, within ChromaDB's rules."""
    slug = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")[:40]
    return f"{base}__{slug}"


async def _redis():
    from autobot_shared.redis_client import get_async_redis_client

    return await get_async_redis_client(database="knowledge", decode_responses=True)


async def switch_status() -> Dict[str, Any]:
    """The current or last switch: state, model, collection, done/total, failed count and ids."""
    client = await _redis()
    raw = await client.hgetall(STATE_KEY) if client is not None else {}
    status: Dict[str, Any] = dict(raw or {})
    for key in ("done", "total", "failed"):
        if key in status:
            status[key] = int(status[key])
    status["failed_ids"] = json.loads(status.get("failed_ids", "[]"))
    status.setdefault("state", "idle")
    return status


async def _set_state(**fields: Any) -> None:
    client = await _redis()
    if client is None:
        raise RuntimeError("the embedding-switch state cannot be written: no Redis")
    await client.hset(STATE_KEY, mapping={k: str(v) for k, v in fields.items()})


async def start_switch(kb, model: str) -> Dict[str, Any]:
    """Start a background rebuild for *model*; refuses rather than queueing a second one."""
    if not is_embedding_model_id(model):
        raise SwitchRefused(f"{model!r} is not an embedding model")
    if (await switch_status()).get("state") == "building":
        raise SwitchRefused("a switch is already running")
    if model == active_embedding_model():
        raise SwitchRefused(f"{model!r} is already the active embedding model")
    collection = target_collection(kb.chromadb_collection.split("__", 1)[0], model)
    client = await _redis()
    await client.delete(STATE_KEY, PENDING_KEY)
    started = datetime.now(tz=timezone.utc).isoformat()
    await _set_state(state="building", model=model, collection=collection, done=0, total=0, failed=0, started=started)
    fire_and_forget(run_switch(kb, model, collection), name=f"embedding-switch:{model}")
    return await switch_status()


async def request_switch_cancel() -> bool:
    """Ask a running build to stop at its next batch; False when none is running."""
    if (await switch_status()).get("state") != "building":
        return False
    await _set_state(cancel="1")
    return True


async def note_write(fact_id: str) -> None:
    """Record a fact written, updated or deleted while a build runs, for replay before cutover."""
    try:
        client = await _redis()
        if client is not None and await client.hget(STATE_KEY, "state") in _NOTING_STATES:
            await client.sadd(PENDING_KEY, fact_id)
    except Exception as exc:  # noqa: BLE001 - a write must not fail because the switch log did
        logger.error("embedding switch could not note write %s (%s); the build will fail its count check", fact_id, exc)


async def run_switch(kb, model: str, collection: str) -> None:
    """The background build; any failure leaves the active space untouched and says why."""
    try:
        store, failed_ids, done = await _build(kb, model, collection)
        if store is None:
            return  # cancelled; the state already says so
        failed_ids += await _replay_pending(store, model)
        if failed_ids:
            reason = f"{len(failed_ids)} fact(s) failed to embed; cutover needs zero failures"
            await _finish("failed", reason=reason, failed=len(failed_ids), failed_ids=failed_ids)
            return
        await _verify_and_cut_over(kb, store, model, collection, done)
    except Exception as exc:  # noqa: BLE001 - recorded, not raised: nobody awaits a background job
        logger.exception("embedding switch to %s failed", model)
        await _finish("failed", reason=f"{type(exc).__name__}: {exc}")


async def _finish(state: str, *, reason: str = "", failed: int | None = None, failed_ids=()) -> None:
    fields: Dict[str, Any] = {"state": state, "finished": datetime.now(tz=timezone.utc).isoformat(), "reason": reason}
    if failed is not None:
        fields["failed"] = failed
    fields["failed_ids"] = json.dumps(list(failed_ids)[:_FAILED_IDS_SHOWN])
    await _set_state(**fields)


async def _build(kb, model: str, collection: str) -> Tuple[Any, List[str], int]:
    from knowledge import fact_store

    await _set_state(total=await fact_store.count_facts())
    store, failed_ids, done = None, [], 0
    async for batch in fact_store.iter_facts(batch_size=SWITCH_BATCH_SIZE):
        if (await switch_status()).get("cancel") == "1":
            await _finish("cancelled", reason="cancelled by request")
            return None, [], done
        nodes, failed = await _embed(model, batch)
        if nodes:
            store = store or await _open_collection(kb, collection, model, len(nodes[0].embedding))
            await asyncio.to_thread(store.add, nodes)
        failed_ids += failed
        done += len(batch)
        await _set_state(done=done, failed=len(failed_ids))
    return store or await _open_collection(kb, collection, model, None), failed_ids, done


async def _embed(model: str, batch: List[Dict[str, Any]]) -> Tuple[list, List[str]]:
    """TextNodes for the facts that embedded, plus the ids of those that did not."""
    from llama_index.core.schema import TextNode

    from knowledge.utils import sanitize_metadata_for_chromadb
    from services.npu_client import generate_embeddings_batch_with_fallback

    vectors = await generate_embeddings_batch_with_fallback([f["content"] for f in batch], model_name=model)
    nodes, failed = [], []
    for fact, vector in zip(batch, vectors):
        if not vector:
            failed.append(fact["fact_id"])
            continue
        meta = {**fact["metadata"], "fact_id": fact["fact_id"], "embedding_model": model}
        nodes.append(
            TextNode(
                id_=fact["fact_id"],
                text=fact["content"],
                embedding=vector,
                metadata=sanitize_metadata_for_chromadb(meta),
            )
        )
    return nodes, failed


async def _open_collection(kb, collection: str, model: str, dim: int | None):
    """The new collection's LlamaIndex store, stamped with the model and dimension it holds."""
    from llama_index.vector_stores.chroma import ChromaVectorStore

    from autobot_shared.embedding_provenance import EmbeddingProvenance, provenance_to_metadata
    from knowledge.backends import get_default_client

    client = await asyncio.to_thread(
        get_default_client, db_path=str(kb.chromadb_path), allow_reset=False, anonymized_telemetry=False
    )
    meta = {**kb._build_hnsw_metadata(), **(provenance_to_metadata(EmbeddingProvenance(model, dim)) if dim else {})}
    raw = await asyncio.to_thread(client.get_or_create_collection, name=collection, metadata=meta)
    return ChromaVectorStore(chroma_collection=getattr(raw, "_raw", raw))


async def _replay_pending(store, model: str) -> List[str]:
    """Re-project every fact written during the build; returns ids that failed to embed."""
    from knowledge import fact_store

    client = await _redis()
    failed: List[str] = []
    while fact_id := await client.spop(PENDING_KEY):
        fact = await fact_store.load_fact(fact_id)
        # by id on the raw collection: LlamaIndex's delete() matches ref_doc_id, which these nodes lack
        await asyncio.to_thread(store._collection.delete, ids=[fact_id])
        if fact is not None:
            nodes, bad = await _embed(model, [{"fact_id": fact_id, **fact}])
            if nodes:
                await asyncio.to_thread(store.add, nodes)
            failed += bad
    return failed


def _settle_seconds() -> float:
    """Long enough for every process to read the new record and rebind (both caches, plus margin)."""
    from autobot_shared.embedding_model import EMBEDDING_OVERRIDE_CACHE_S
    from knowledge.embedding_space_binding import SPACE_CHECK_INTERVAL_S

    return EMBEDDING_OVERRIDE_CACHE_S + SPACE_CHECK_INTERVAL_S + 2.0


async def _verify_and_cut_over(kb, store, model: str, collection: str, done: int) -> None:
    """Cut over only when the new collection holds exactly one vector per recorded fact."""
    from knowledge import fact_store
    from knowledge.embedding_space_binding import rebind

    expected, held = await fact_store.count_facts(), await asyncio.to_thread(store._collection.count)
    if held != expected:
        await _finish("failed", reason=f"the new collection holds {held} vectors for {expected} facts")
        return
    await _set_state(state="settling")
    space = await asyncio.to_thread(set_embedding_model_override, model, collection)
    await rebind(kb, space)
    await asyncio.sleep(_settle_seconds())  # every process has noticed the record by now
    late = await _replay_pending(store, model)
    reason = f"{held} facts re-embedded with {model}" + (f"; {len(late)} late write(s) failed to embed" if late else "")
    await _finish("completed", reason=reason, failed=len(late), failed_ids=late)
    logger.warning("Embedding model switched to %s (%d facts in %s, #19379)", model, held, collection)
