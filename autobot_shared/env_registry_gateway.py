# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Gateway / channel-connector AUTOBOT_* environment variable registrations.

Split out of ``env_registry.py`` (#19010) the same way #18765 split the network
block: that module sits at its frozen size ceiling, so the gateway connector
block moved here to make room for the Telegram polling ingest variables.

Registration contract (import side effect, ordering): see ``env_registry`` (#16415).
"""

from __future__ import annotations

from autobot_shared.env_registry import EnvVarSpec, register_env_var

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_IMESSAGE_ENABLED",
        type=bool,
        default=False,
        description=(
            "Opt in to the iMessage gateway adapter. Off by default: it is "
            "macOS-only and needs Full Disk Access to the Messages database."
        ),
        component="gateway",
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_SIGNAL_ENABLED",
        type=bool,
        default=False,
        description=(
            "Opt in to the Signal gateway adapter. Off by default: it needs a "
            "running signal-cli daemon and a registered number."
        ),
        component="gateway",
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_MATRIX_E2EE",
        type=bool,
        default=False,
        description=(
            "Opt in to end-to-end encryption for the Matrix adapter. Off by "
            "default because E2EE needs the optional olm dependency and a "
            "persisted device store."
        ),
        component="gateway",
    )
)

# --- Telegram polling ingest (#19010) ----------------------------------------

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_TELEGRAM_POLL_TIMEOUT_SECONDS",
        type=int,
        default=20,
        description=(
            "Long-poll window of each Telegram getUpdates call in polling ingest "
            "mode. Capped below the poller's 30s leader-lease lifetime so a full "
            "wait still renews the lease before another worker could take it "
            "(services/telegram_polling.py)."
        ),
        component="gateway",
        range=(1, 25),
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_TELEGRAM_POLL_IDLE_SECONDS",
        type=float,
        default=15.0,
        description=(
            "How long the Telegram poller leader waits between checks of the "
            "stored ingest mode while it is not 'polling'. A switch to polling "
            "takes effect within this window; capped below the lease lifetime."
        ),
        component="gateway",
        range=(1, 25),
    )
)

register_env_var(
    EnvVarSpec(
        name="AUTOBOT_TELEGRAM_MAX_CONCURRENT_REPLIES",
        type=int,
        default=4,
        description=(
            "Most Telegram replies generated at once in polling ingest mode. "
            "Messages in one chat are always answered one at a time, in order; "
            "this bounds concurrency across chats."
        ),
        component="gateway",
        range=(1, 64),
    )
)
