# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""``get_user_permissions`` resolves a DB role's permissions (#18259).

The contrast tests pin why the fix was needed: the line both forks carried,
``role.Permission``, raises on a real ``Role``.
"""

from unittest.mock import MagicMock

import pytest

from autobot_shared.user_management.base_service import TenantContext
from autobot_shared.user_management.models.role import Role
from autobot_shared.user_management.user_service import UserService


class _Bound(UserService):
    user_model = MagicMock()


async def test_fixed_query_returns_the_roles_permissions(grant_db):
    await grant_db.add_role("auditor", ["audit:read", "users:read"])
    user_id = await grant_db.add_user("ann", ["auditor"])
    service = _Bound(grant_db.session, TenantContext())
    assert await service.get_user_permissions(user_id) == {"audit:read", "users:read"}


async def test_permissions_are_the_union_of_every_role(grant_db):
    await grant_db.add_role("reader", ["users:read", "teams:read"])
    await grant_db.add_role("writer", ["users:read", "users:write"])
    await grant_db.add_role("empty", [])
    user_id = await grant_db.add_user("bob", ["reader", "writer", "empty"])
    service = _Bound(grant_db.session, TenantContext())
    assert await service.get_user_permissions(user_id) == {"users:read", "users:write", "teams:read"}


async def test_a_user_without_roles_gets_nothing(grant_db):
    await grant_db.add_role("reader", ["users:read"])
    user_id = await grant_db.add_user("cid", [])
    assert await _Bound(grant_db.session, TenantContext()).get_user_permissions(user_id) == set()


def test_contrast_the_legacy_line_raises():
    """``role.Permission`` -- the pre-#18259 lookup -- is not an attribute of ``Role``.

    Instance lookup falls through to the class, so the class-level access is the
    same failure without configuring every mapper in the registry.
    """
    with pytest.raises(AttributeError, match="Permission"):
        Role.Permission  # noqa: B018  # the legacy line's attribute access
