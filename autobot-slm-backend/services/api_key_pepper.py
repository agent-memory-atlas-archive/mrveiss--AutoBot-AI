# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Resolve the API-key HMAC pepper from the SLM secrets store (#19120, #16299).

The pepper is a rotatable credential, so it lives in the SLM secrets store
(``SystemSecret``, managed on ``/slm/settings/admin/secrets``) like every other
system secret. Resolution order:

1. the secrets store (``retrieve_secret``) -- always wins;
2. ``SLM_HMAC_API_KEY_SECRET`` from the environment, imported into the store
   ONCE when the store holds no value (a migration of an existing value);
3. neither: ``None``. Callers fail closed for new keys.

Nothing here generates, seeds or rotates a value: creating the pepper is a human
action (the secrets page). The value is never logged.

Rotation limitation: ``SystemSecret`` keeps a single value, with no previous
one, so there is no dual-verify window. See
``autobot-slm-backend/docs/api-key-pepper.md``.

Stdlib logging, deliberately: the SLM test harness mocks ``config``
(see ``generated_system_secrets.py``).
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

#: ``SystemSecret.key`` the operator sets on the secrets page.
HMAC_PEPPER_KEY = "slm_hmac_api_key_secret"  # nosec B105  # key name, not a credential
HMAC_PEPPER_DESCRIPTION = "Pepper for API-key hashing (#19120). Required to create API keys."
SECRETS_PAGE_PATH = "/slm/settings/admin/secrets"

WARN_UNCONFIGURED = (
    "API-key pepper is not configured: add the secret '%s' on the SLM page %s "
    "(or set SLM_HMAC_API_KEY_SECRET once, to be imported). New API keys cannot be "
    "created and existing keys are not re-hashed until it is set."
)


async def resolve_hmac_api_key_secret(session: AsyncSession) -> str | None:
    """Return the pepper (store first, else one-time env import), or ``None``."""
    from services.system_secrets_vault import retrieve_secret

    stored = await retrieve_secret(session, HMAC_PEPPER_KEY)
    if stored:
        return stored

    from config import settings

    env_value = settings.hmac_api_key_secret
    if not env_value:
        logger.warning(WARN_UNCONFIGURED, HMAC_PEPPER_KEY, SECRETS_PAGE_PATH)
        return None
    await _import_env_value(session, env_value)
    return env_value


async def _import_env_value(session: AsyncSession, value: str) -> None:
    """Insert the env value into the store; a concurrent insert wins silently."""
    from models.database import SystemSecret
    from services.encryption import encrypt_data
    from services.system_secrets_vault import mirror_secret_to_vault

    row = SystemSecret(
        key=HMAC_PEPPER_KEY,
        encrypted_value=encrypt_data(value),
        category="system",
        description=HMAC_PEPPER_DESCRIPTION,
    )
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError:
        logger.info("api-key-pepper: store already holds %s; import skipped", HMAC_PEPPER_KEY)
        return
    await mirror_secret_to_vault(HMAC_PEPPER_KEY, value)
    logger.info(
        "AUDIT api-key-pepper: imported SLM_HMAC_API_KEY_SECRET from env into secret '%s' "
        "(one-time import; value not logged)",
        HMAC_PEPPER_KEY,
    )
