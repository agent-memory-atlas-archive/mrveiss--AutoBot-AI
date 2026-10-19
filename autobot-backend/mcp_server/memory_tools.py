# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Memory-graph MCP tools (#5072), split out of ``autobot_server`` to keep that file under its size ceiling.

Every graph read runs under ``current_owner()`` -- the authenticated MCP principal bound for
the request (#18825). A caller with no identity (the stdio transport) gets ``OwnerScopeRequired``,
which the dispatcher answers with a 403-class error: there is no unscoped graph read here.
``memory.verbatim_search`` is a different store (#16727) and keeps its own explicit sentinel.
"""

from typing import Any, Dict

from mcp_server.principal import current_owner


class MemoryToolsMixin:
    """The ``memory.*`` tool handlers; mixed into ``AutoBotMCPServer``."""

    async def _memory_entity_lookup(self, name: str) -> Any:
        from api.schemas_knowledge import MemoryEntityLookupRequest
        from autobot_memory_graph import AutoBotMemoryGraph

        name = MemoryEntityLookupRequest(name=name).name
        owner = current_owner()  # the authenticated principal; fails closed without one (#18825)
        graph = AutoBotMemoryGraph()
        await graph.initialize()
        entity = await graph.get_entity(entity_name=name, include_relations=True, owner=owner)
        if entity is None:
            return {"error": "Entity not found", "name": name}
        return entity

    async def _memory_timeline(self, entity: str, range: str | None = None) -> Any:
        from api.schemas_knowledge import MemoryTimelineRequest
        from autobot_memory_graph import AutoBotMemoryGraph

        args = MemoryTimelineRequest(entity=entity, range=range)
        entity, range = args.entity, args.range
        owner = current_owner()  # the authenticated principal; fails closed without one (#18825)
        graph = AutoBotMemoryGraph()
        await graph.initialize()
        base = await graph.get_entity(entity_name=entity, include_relations=True, owner=owner)
        if base is None:
            return {"error": "Entity not found", "entity": entity}

        entity_id = base.get("id", "")
        relations_data = await graph.get_relations(entity_id, direction="both")
        relations = relations_data.get("relations", [])

        # Gather linked entity IDs and their details
        neighbour_ids = {r.get("from_entity") for r in relations} | {r.get("to_entity") for r in relations}
        neighbour_ids.discard(entity_id)

        neighbours = []
        for nid in list(neighbour_ids)[:50]:
            ent = await graph.get_entity(entity_id=nid, owner=owner)
            if ent:
                neighbours.append(ent)

        # Sort by created_at ascending (timeline order)
        neighbours.sort(key=lambda e: (e.get("metadata") or {}).get("created_at", ""), reverse=False)
        if range:
            # range is "start/end"; filter by created_at string prefix comparison
            parts = range.split("/", 1)
            start, end = (parts[0], parts[1]) if len(parts) == 2 else (parts[0], None)
            neighbours = [
                e
                for e in neighbours
                if (e.get("metadata") or {}).get("created_at", "") >= start
                and (end is None or (e.get("metadata") or {}).get("created_at", "") <= end)
            ]

        return {"entity": base, "timeline": neighbours, "count": len(neighbours)}

    async def _memory_related(self, entity: str, depth: int = 2) -> Any:
        from api.schemas_knowledge import MemoryRelatedRequest
        from autobot_memory_graph import AutoBotMemoryGraph

        # #13762: the entity name was unbounded. ``depth`` keeps its existing
        # clamp rather than becoming a rejection, so the model's ge/le is the
        # declared contract and the clamp is what enforces it.
        args = MemoryRelatedRequest(entity=entity, depth=max(1, min(depth, 5)))
        entity, depth = args.entity, args.depth
        owner = current_owner()  # the authenticated principal; fails closed without one (#18825)
        graph = AutoBotMemoryGraph()
        await graph.initialize()

        root = await graph.get_entity(entity_name=entity, include_relations=True, owner=owner)
        if root is None:
            return {"error": "Entity not found", "entity": entity}

        visited: Dict[str, Any] = {}
        frontier = [root.get("id", "")]

        for _ in range(depth):
            next_frontier = []
            for eid in frontier:
                if eid in visited:
                    continue
                ent = await graph.get_entity(entity_id=eid, owner=owner)
                if not ent:
                    continue
                visited[eid] = ent
                rels = await graph.get_relations(eid, direction="both")
                for rel in rels.get("relations", []):
                    cand = rel.get("to_entity") or rel.get("from_entity")
                    if cand and cand not in visited:
                        next_frontier.append(cand)
            frontier = next_frontier
            if not frontier:
                break

        return {
            "root": root,
            "related": list(visited.values()),
            "count": len(visited),
        }

    async def _memory_path(
        self,
        from_entity: str,
        to_entity: str,
        relation: str | None = None,
        max_depth: int = 6,
        direction: str = "both",
    ) -> Any:
        """Shortest relationship path between two entities (#13474).

        Delegates to AutoBotMemoryGraph.find_path so name resolution and path
        serialisation are not duplicated against the Graph-RAG ``/path``
        endpoint. #13762 extends that to the input contract: the arguments go
        through ``GraphRAGPathRequest``, the same model the REST route binds, so
        the two surfaces cannot drift on what a valid request is. ``max_depth``
        stays clamped rather than rejected — an untrusted caller must not be
        able to request an unbounded traversal, but asking for too much is not
        an error the way an unbounded entity name is.
        """
        from api.schemas_knowledge import GraphRAGPathRequest
        from autobot_memory_graph import AutoBotMemoryGraph

        allowed_directions = ("outgoing", "incoming", "both")
        if direction not in allowed_directions:
            return {"error": "Invalid direction", "direction": direction, "allowed": list(allowed_directions)}

        args = GraphRAGPathRequest(
            from_entity=from_entity,
            to_entity=to_entity,
            relation=relation,
            max_depth=max(1, min(max_depth, 10)),
            direction=direction,
        )
        owner = current_owner()  # the authenticated principal; fails closed without one (#18825)

        graph = AutoBotMemoryGraph()
        await graph.initialize()
        return await graph.find_path(
            from_entity=args.from_entity,
            to_entity=args.to_entity,
            relation=args.relation,
            max_depth=args.max_depth,
            direction=args.direction,
            owner=owner,
        )

    async def _memory_verbatim_search(self, query: str, session_filter: str | None = None) -> Any:
        from api.schemas_knowledge import MemoryVerbatimSearchRequest
        from memory.verbatim_store import UNSCOPED_ALL_USERS, VerbatimStore

        args = MemoryVerbatimSearchRequest(query=query, session_filter=session_filter)
        store = VerbatimStore()
        results = await store.search(args.query, user_id=UNSCOPED_ALL_USERS, session_filter=args.session_filter)
        return {"results": results, "count": len(results)}
