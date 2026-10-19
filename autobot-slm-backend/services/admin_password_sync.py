# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Keep the SLM admin password in step with SLM_ADMIN_PASSWORD, revoking older sessions (#18672).

Startup re-synced the hash from the secrets file on every start and recorded no password
epoch, so a token issued before an admin password change stayed valid until it expired.
The API path (UserService.change_password) already records the epoch (#12924); this does
the same, and only when the password actually changed -- not on every start.
"""

import logging

from autobot_shared.auth.jwt_core import verify_password
from autobot_shared.user_management.password_epoch import set_password_epoch
from services.auth import auth_service

logger = logging.getLogger(__name__)


async def sync_admin_password(db, user, password: str) -> bool:
    """Rehash *user* to *password* if it differs and revoke earlier tokens; True if changed."""
    if user.password_hash and verify_password(password, user.password_hash):
        return False
    user.password_hash = auth_service.hash_password(password)
    await db.flush()
    await set_password_epoch(user.username)
    logger.info("Admin password synced from SLM_ADMIN_PASSWORD; sessions issued before it are revoked")
    return True
