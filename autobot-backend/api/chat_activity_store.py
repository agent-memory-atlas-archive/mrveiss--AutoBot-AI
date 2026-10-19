# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Activity storage for chat sessions (split out of ``api.chat_sessions`` in #18825)."""

from typing import Dict, List

from fastapi import HTTPException

from api.schemas_chat import ActivityCreate
from autobot_memory_graph import AutoBotMemoryGraph, OwnerScope, OwnerScopeRequired
from autobot_shared.logging_manager import get_logger

logger = get_logger(__name__)


def activity_principal_id(ownership: Dict) -> str:
    """Id of the authenticated principal behind a validated session request (#18825).

    The activity body carries its own ``user_id``; trusting it let any caller attribute
    activity (and graph ownership) to someone else, so only ``ownership["user_data"]`` counts.
    """
    try:
        return OwnerScope.from_auth_user((ownership or {}).get("user_data")).user_id
    except OwnerScopeRequired:
        raise HTTPException(status_code=403, detail="Authenticated user required")


async def _store_single_activity(
    memory_graph: AutoBotMemoryGraph,
    session_id: str,
    activity: ActivityCreate,
    user_id: str,
) -> Dict:
    """
    Store a single activity in memory graph.

    Issue #665: Extracted helper for activity storage.
    #18825: ``user_id`` is the authenticated principal; ``activity.user_id`` (request body) is
    client-supplied and never used for attribution or ownership.

    Returns:
        Entity creation result dict
    """
    return await memory_graph.create_activity_entity(
        activity_type=f"{activity.type}_activity",
        session_id=session_id,
        user_id=user_id,
        content=activity.content,
        secrets_used=activity.secrets_used,
        metadata={
            "frontend_id": activity.activity_id,
            "frontend_timestamp": activity.timestamp,
            **(activity.metadata or {}),
        },
    )


async def _process_activity_batch(
    memory_graph: AutoBotMemoryGraph,
    session_id: str,
    activities: List[ActivityCreate],
    request_id: str,
    user_id: str,
) -> Dict:
    """
    Process a batch of activities and store them in memory graph.

    Issue #620.

    Args:
        memory_graph: Memory graph instance for storage
        session_id: Session ID for the activities
        activities: List of activities to process
        request_id: Request ID for logging
        user_id: Authenticated principal id used for every activity (#18825)

    Returns:
        Dict with stored_count, failed_count, and stored_ids
    """
    stored_count = 0
    failed_count = 0
    stored_ids: List[str] = []

    for activity in activities:
        try:
            await _store_single_activity(memory_graph, session_id, activity, user_id)
            stored_count += 1
            stored_ids.append(activity.activity_id)
        except Exception as activity_error:
            logger.warning(
                "[%s] Failed to store activity %s: %s",
                request_id,
                activity.activity_id,
                activity_error,
            )
            failed_count += 1

    return {
        "stored_count": stored_count,
        "failed_count": failed_count,
        "stored_ids": stored_ids,
    }
