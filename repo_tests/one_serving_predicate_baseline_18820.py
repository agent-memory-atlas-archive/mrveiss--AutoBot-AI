# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Frozen allowlist of the trust/validity readers that exist outside `knowledge/serving.py` (#18820).

Each key is `<repo path>::<field>::<kind>`; the value is the reason it is not a second serving
predicate. The list may only SHRINK: a new match fails the guard, and an entry whose reader left
the tree (or stopped matching) fails it too until the line is deleted. `FROZEN_TOTAL` caps the
length so a stale-then-refilled list cannot grow quietly.

`READ` = a load of `.valid_to` / `.valid_until` / `.valid_from` / `.is_active` / `.verification_status`
(attribute, `x["f"]` or `x.get("f")`). `REJECT_CMP` / `REJECT_WHERE` have NO entries: today only
`knowledge/serving.py` compares a trust state to "rejected". Entries marked MIGRATE are serving-shaped
and should move onto the predicate; the rest are not facts or not a serving decision.
"""

from __future__ import annotations

_AGENT_RUNTIME = "is_active of a live agent-loop assertion, agent channel or terminal session; not a knowledge fact"
_CREDENTIAL = "is_active of a stored credential or API key (secrets store); not a knowledge fact"
_ACCOUNT = "is_active of a user, organization or person account; not a knowledge fact"
_CONFIG = "is_active of a share link, model record or project phase; not a knowledge fact"
_TRUST = (
    "reads verification_status for review queue, export or retention; not a serving decision "
    "(never compares to rejected)"
)
_ATOMIC = (
    "AtomicFact model: the extraction pipeline's own validity bookkeeping (invalidate, "
    "serialise, active_facts property); MIGRATE the active_facts view onto knowledge.serving if "
    "it gains a serving caller"
)
_TEMPORAL = (
    "temporal-invalidation job: ages facts and persists valid_until; it decides what to INVALIDATE, not what to serve"
)
_ACTIVE_ONLY = (
    "MIGRATE: an active_only filter on extracted facts returned to callers is a serving-shaped "
    "decision; route it through knowledge.serving (follow-up under #18820)"
)

ALLOWLIST: dict[str, str] = {
    "autobot-backend/agent_loop/belief_state.py::is_active::READ": _AGENT_RUNTIME,
    "autobot-backend/agent_loop/loop.py::is_active::READ": _AGENT_RUNTIME,
    "autobot-backend/agent_loop/types.py::is_active::READ": _AGENT_RUNTIME,
    "autobot-backend/agents/knowledge_extraction_agent.py::is_active::READ": _ACTIVE_ONLY,
    "autobot-backend/api/agent_config.py::is_active::READ": _CONFIG,
    "autobot-backend/api/chat_shared_links.py::is_active::READ": _CONFIG,
    "autobot-backend/api/coordination_issue_claims_credential.py::is_active::READ": _CREDENTIAL,
    "autobot-backend/api/knowledge_verification.py::verification_status::READ": _TRUST,
    "autobot-backend/api/phases.py::is_active::READ": _CONFIG,
    "autobot-backend/api/project_state.py::is_active::READ": _CONFIG,
    "autobot-backend/api/terminal.py::is_active::READ": _AGENT_RUNTIME,
    "autobot-backend/api/user_management/organizations.py::is_active::READ": _ACCOUNT,
    "autobot-backend/api/user_management/users.py::is_active::READ": _ACCOUNT,
    "autobot-backend/api/user_provider_credentials.py::is_active::READ": _CREDENTIAL,
    "autobot-backend/benchmarks/benchmark_belief_state.py::is_active::READ": _AGENT_RUNTIME,
    "autobot-backend/knowledge/adapters/okf_adapter.py::verification_status::READ": _TRUST,
    "autobot-backend/knowledge/facts.py::verification_status::READ": _TRUST,
    "autobot-backend/llc/api/companies.py::is_active::READ": _ACCOUNT,
    "autobot-backend/llm_shared/run_credential_loader.py::is_active::READ": _CREDENTIAL,
    "autobot-backend/models/atomic_fact.py::is_active::READ": _ATOMIC,
    "autobot-backend/models/atomic_fact.py::valid_from::READ": _ATOMIC,
    "autobot-backend/models/atomic_fact.py::valid_until::READ": _ATOMIC,
    "autobot-backend/models/chat_shared_link.py::is_active::READ": _CONFIG,
    "autobot-backend/models/ml_model.py::is_active::READ": _CONFIG,
    "autobot-backend/project_state_manager.py::is_active::READ": _CONFIG,
    "autobot-backend/protocols/agent_channels.py::is_active::READ": _AGENT_RUNTIME,
    "autobot-backend/protocols/agent_communication.py::is_active::READ": _AGENT_RUNTIME,
    "autobot-backend/routers/model_management.py::is_active::READ": _CONFIG,
    "autobot-backend/services/credential_read.py::is_active::READ": _CREDENTIAL,
    "autobot-backend/services/credential_reconcile.py::is_active::READ": _CREDENTIAL,
    "autobot-backend/services/credential_write.py::is_active::READ": _CREDENTIAL,
    "autobot-backend/services/fact_extraction_service.py::is_active::READ": _ACTIVE_ONLY,
    "autobot-backend/services/json_secrets_read.py::is_active::READ": _CREDENTIAL,
    "autobot-backend/services/llc_secrets_read.py::is_active::READ": _CREDENTIAL,
    "autobot-backend/services/provider_key_vault.py::is_active::READ": _CREDENTIAL,
    "autobot-backend/services/secrets_access_audit.py::is_active::READ": _CREDENTIAL,
    "autobot-backend/services/secrets_coordinator.py::is_active::READ": _CREDENTIAL,
    "autobot-backend/services/secrets_principal_resolver.py::is_active::READ": _CREDENTIAL,
    "autobot-backend/services/temporal_invalidation_service.py::valid_from::READ": _TEMPORAL,
    "autobot-backend/services/temporal_invalidation_service.py::valid_until::READ": _TEMPORAL,
    "autobot-backend/tools/terminal_tool.py::is_active::READ": _AGENT_RUNTIME,
    "autobot_shared/user_management/login_eligibility.py::is_active::READ": _ACCOUNT,
    "autobot_shared/user_management/models/api_key.py::is_active::READ": _ACCOUNT,
    "autobot_shared/user_management/organization_service.py::is_active::READ": _ACCOUNT,
    "autobot_shared/user_management/user_service.py::is_active::READ": _ACCOUNT,
}

FROZEN_TOTAL = 45
