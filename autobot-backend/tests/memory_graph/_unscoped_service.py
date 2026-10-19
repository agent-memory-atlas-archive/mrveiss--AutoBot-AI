# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Test helper: a GraphRAGService whose graph reads default to the unscoped sentinel (#18825).

Existing service tests exercise traversal, blending and timeouts, not scoping, so they read
across owners on purpose. Owner scoping itself is proved in ``test_owner_scope_18825.py``.
"""

from functools import partial
from typing import Any

from autobot_memory_graph.owner_scope import UNSCOPED_ALL_USERS
from services.graph_rag_service import GraphRAGService as _BaseGraphRAGService

_OWNER_SCOPED_METHODS = (
    "graph_aware_search",
    "find_connection_path",
    "_fetch_related_entities_parallel",
    "_expand_via_graph",
    "_extract_entities_from_results",
)


def unscoped(service: Any) -> Any:
    """Bind ``owner=UNSCOPED_ALL_USERS`` as the default for the service's owner-scoped methods."""
    for name in _OWNER_SCOPED_METHODS:
        setattr(service, name, partial(getattr(service, name), owner=UNSCOPED_ALL_USERS))
    return service


class UnscopedGraphRAGService(_BaseGraphRAGService):
    """GraphRAGService whose owner-scoped methods default to the unscoped sentinel (test double)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        unscoped(self)


#: Tests import this name so they exercise the real service API without repeating ``owner=`` everywhere.
GraphRAGService = UnscopedGraphRAGService
