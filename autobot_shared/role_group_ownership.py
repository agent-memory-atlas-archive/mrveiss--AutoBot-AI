# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Which role owns an ansible group's service (#19197).

A DETECTED role token grants a group only while the group's own service is
itself detected on the node. ``redis``/``database`` are one service here:
``deploy-database`` installs redis-stack-server on ``database`` hosts and
``role_redis_active`` fires on either group. So a detected ``slm-database`` or
``postgres`` (postgresql, left behind by a mis-targeted play) must not put a node
into them. Declared roles are never filtered.

Audit of the role->group map for tokens granting a service they do not run:
  slm-database -> redis, database  (postgresql)         filtered here
  postgres     -> database         (postgresql)         filtered here
  chromadb, tts-worker -> ai_stack/aiml/ai; celery, scheduler, autobot_shared ->
  backend/main: already withheld from detection by ``_DECLARED_ONLY_GROUPS``.
"""

from typing import Any

from autobot_shared.logging_manager import get_logger

logger = get_logger(__name__)

GROUP_OWNERS: dict[str, frozenset] = {
    "redis": frozenset({"redis"}),
    "database": frozenset({"redis"}),
}


def normalise_tokens(tokens: Any) -> set[str]:
    return {t.strip().lower() for t in (tokens or []) if t and t.strip()}


def withhold_unowned_groups(groups: set[str], detected_tokens: Any) -> set[str]:
    """Drop owner-gated groups whose owning service is not itself detected."""
    present = normalise_tokens(detected_tokens)
    return {g for g in groups if not GROUP_OWNERS.get(g) or GROUP_OWNERS[g] & present}


def warn_stale_detected_roles(node: Any) -> list[str]:
    """Log roles a node runs but does not declare; return them. Never removes anything (#17038)."""
    declared = normalise_tokens(getattr(node, "roles", None))
    stale = sorted(normalise_tokens(getattr(node, "detected_roles", None)) - declared)
    if stale:
        logger.warning(
            "node %s runs roles it does not declare: %s -- assign or remove them; "
            "a detected service no longer grants groups for other services (#19197)",
            getattr(node, "node_id", "?"),
            ", ".join(stale),
        )
    return stale


def declared_and_detected(node: Any) -> tuple[list[str], list[str]]:
    """(declared, detected) role tokens for a node; warns on stale detections."""
    warn_stale_detected_roles(node)
    return list(getattr(node, "roles", None) or []), list(getattr(node, "detected_roles", None) or [])


def split_node_role_rows(rows: list, declared: list[str]) -> tuple[list[str], list[str]]:
    """(declared, detected-only) tokens from NodeRole rows: ``manual`` or in Node.roles is declared."""
    declared_tokens = list(declared)
    detected_tokens: list[str] = []
    for nr in rows:
        if nr.role_name in declared:
            continue
        manual = (getattr(nr, "assignment_type", None) or "auto") == "manual"
        (declared_tokens if manual else detected_tokens).append(nr.role_name)
    return declared_tokens, detected_tokens
