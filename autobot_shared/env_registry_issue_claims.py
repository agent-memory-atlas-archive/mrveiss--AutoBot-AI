# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Issue-claims CLI API-transport AUTOBOT_* registrations (#18811).

A sibling of ``env_registry_ledger`` (which imports this module); ``env_registry.py`` is closed to new
entries. The registration contract is the one ``env_registry`` states (#16415).
"""

from __future__ import annotations

from autobot_shared.env_registry import EnvVarSpec, register_env_var

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_ISSUE_CLAIMS_API_URL",
        type=str,
        default="",
        description=(
            "Base URL of AutoBot's backend for the issue-claims CLI (#18811). When set, "
            "`python -m autobot_shared.coordination.issue_claims` talks to /api/coordination/issue-claims "
            "instead of Redis, so a developer checkout needs no registry credential. Never derived from "
            "the SSOT backend URL. Unset keeps the direct Redis transport."
        ),
        component="orchestration",
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_ISSUE_CLAIMS_TOKEN_FILE",
        type=str,
        default="",
        description=(
            "Path of a file holding the bearer token the issue-claims CLI sends to the API (#18811). "
            "Read from the file on each run; the token is never taken from argv or an environment value, "
            "and never printed. Required when AUTOBOT_ISSUE_CLAIMS_API_URL is set."
        ),
        component="orchestration",
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_ISSUE_CLAIMS_API_TIMEOUT_S",
        type=int,
        default=10,
        description="Per-request timeout of the issue-claims CLI's API transport; expiry fails closed (#18811).",
        component="orchestration",
        range=(1, 120),
    )
)
