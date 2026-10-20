# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The ONE answer for "the revocation store could not answer" (#18892).

Both backends fail CLOSED when a revocation check (the jti denylist, the
password epoch) reaches no verdict (#16387, #16411): the token is never
accepted. What differs from "this token is bad" is what the CLIENT must do. A
401 tells it the credential is dead, so it clears a perfectly valid session and
redirects to login. A 503 + ``Retry-After`` tells it the server could not
decide, so it keeps the session and retries. 401 stays for an invalid,
expired or revoked token.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from autobot_shared.env_utils import env_int

#: Stable machine-readable detail clients match on; never reworded.
REVOCATION_UNAVAILABLE_DETAIL = "revocation_check_unavailable"

#: Seconds a client is told to wait before retrying (``Retry-After``).
REVOCATION_RETRY_AFTER_SECONDS: int = env_int("AUTOBOT_REVOCATION_RETRY_AFTER_SECONDS", 5)


def revocation_unavailable_error() -> HTTPException:
    """The 503 to raise when a revocation check cannot run. The caller denies the token."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=REVOCATION_UNAVAILABLE_DETAIL,
        headers={"Retry-After": str(max(1, REVOCATION_RETRY_AFTER_SECONDS))},
    )
