# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Stamp a new legacy-store secret with the authenticated principal (#19476)."""

from __future__ import annotations

from typing import Any, Dict

from api.schemas_system import SecretCreateRequest


def stamp_owner(request: SecretCreateRequest, user: Dict[str, Any] | None) -> SecretCreateRequest:
    """Return ``request`` owned by ``user`` (from ``get_user_from_request``).

    The authenticated principal wins over a body-asserted ``owner_id``/``org_id``:
    the body is self-asserted, the principal is not. With no resolvable user the
    request is returned unchanged.
    """
    user_id = (user or {}).get("user_id")
    if not user_id:
        return request
    update: Dict[str, Any] = {"owner_id": str(user_id)}
    org_id = (user or {}).get("org_id")
    if org_id:
        update["org_id"] = str(org_id)
    return request.model_copy(update=update)
