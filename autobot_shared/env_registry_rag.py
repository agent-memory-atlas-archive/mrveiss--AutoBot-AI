# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""RAG graph-retrieval and KB-ingest extraction AUTOBOT_* environment variable registrations (#18141, #18826).

Sibling of ``env_registry_ai.py`` (which sits at the 600-line ceiling), imported by
``env_registry`` itself like every split module, so registration happens whenever it loads.
"""

from __future__ import annotations

from autobot_shared.env_registry import EnvVarSpec, register_env_var

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_RAG_GRAPH_WEIGHT",
        type=float,
        default=0.3,
        description="Graph-signal weight in the one pre-rerank vector+graph blend (RAGConfig.graph_weight, #18141).",
        component="rag",
        range=(0.0, 1.0),
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_RAG_GRAPH_TIMEOUT_SECONDS",
        type=float,
        default=10.0,
        description="Default graph-RAG search deadline in seconds (RAGConfig.graph_timeout_seconds, #18141).",
        component="rag",
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_HTML_EXTRACT_MAX_BYTES",
        type=int,
        default=10 * 1024 * 1024,
        description=(
            "Largest HTML input, in UTF-8 bytes, the one HTML extractor parses; a longer page is "
            "truncated at a character and tag boundary and yields its leading content "
            "(media/document/html_extraction.py, #18826)."
        ),
        component="knowledge",
        range=(64 * 1024, 256 * 1024 * 1024),
    )
)
