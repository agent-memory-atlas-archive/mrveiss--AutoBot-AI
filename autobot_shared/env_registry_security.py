# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Security AUTOBOT_* environment variable registrations.

Split out of ``env_registry.py`` rather than added inline, for the reason that
file's own docstring gives: a new variable goes in an
``env_registry_<component>.py`` sibling. The mechanical reason is the one
``env_registry_logging.py`` records — ``env_registry.py`` sits at its
grandfathered file-size ceiling (#14236) with no slack, and the ratchet forbids
raising a ceiling to make room, so an inline registration cannot land there at
all.

#13602 added the first ``component="security"`` variable, so this is the
component's first home rather than a move of existing entries.

Registration contract (import side effect, ordering): see ``env_registry`` (#16415).
"""

from __future__ import annotations

from autobot_shared.env_registry import EnvVarSpec, register_env_var

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_LOG_VALUE_MAX_CHARS",
        type=int,
        default=256,
        description=(
            "Longest untrusted value sanitize_log_value() will interpolate into a log line "
            "(#13602). Beyond it the value is truncated with an explicit marker, so the "
            "subject of a rejected request cannot pad the log store out. A negative value is "
            "rejected in favour of the default: disabling the bound would defeat the guard."
        ),
        component="security",
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_EGRESS_INTERNAL_ORIGINS_TTL_S",
        type=int,
        default=60,
        description=(
            "Seconds the egress policy reuses the origins returned by registered internal-origin "
            "providers (e.g. SLM-managed nodes) before asking them again (#17576). Static origins "
            "from the SSOT are never cached. A provider that raises is logged and skipped."
        ),
        component="security",
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_EGRESS_PROVIDER_TIMEOUT_S",
        type=int,
        default=5,
        description=(
            "Seconds one internal-origin provider may run before it is abandoned for this refresh "
            "(#17576). A provider that times out or raises is logged and skipped; the static SSOT "
            "origins still apply."
        ),
        component="security",
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_PROVIDER_KEY_HYDRATION_TIMEOUT_S",
        type=int,
        default=30,
        description=(
            "Seconds a process waits for its one-time provider-key vault hydration before treating the "
            "vault as unreachable (#18094). On timeout every vault-resolved key is refused (fail closed); a "
            "hydration that finishes later still swaps its result in."
        ),
        component="security",
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_PROVIDER_KEY_REHYDRATE_BACKOFF_S",
        type=int,
        default=30,
        description=(
            "First delay before a process whose provider-key vault was unreachable tries to hydrate again on a "
            "later key resolution (#18094). Doubles after each failed retry, capped at 20x this value; a "
            "successful hydration resets it. Until a retry succeeds, keys stay refused, never served from env."
        ),
        component="security",
    )
)
