# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The one definition of "which permission names does a DB role grant" (#18259).

Before #18259, ``UserService.get_user_permissions`` resolved each name with
``select(role.Permission)``. ``Role`` has no ``Permission`` attribute, so every
user holding a role with at least one ``role_permissions`` row raised, and the
RBAC middleware turned that into an empty (deny-all) permission set.

Both the fixed ``get_user_permissions`` and the operator's grant-change report
(``permission_grant_report``) build their statements here, so the report shows
exactly what the fix grants and cannot drift from it.

The statements are Core selects over the mapped classes' own tables rather than
ORM entity selects. An ORM select configures every mapper in the registry, and
``Role`` relates to ``Organization``/``User``, which only each service's concrete
subclass maps. Core selects need no mapper configuration, so the same statement
runs inside either service and in the stand-alone report CLI, which imports
neither service.
"""

import uuid

from sqlalchemy import Select, func, select

from autobot_shared.user_management.models.role import Permission, Role, RolePermission, UserRole

_PERMISSIONS = Permission.__table__
_ROLES = Role.__table__
_ROLE_PERMISSIONS = RolePermission.__table__
_USER_ROLES = UserRole.__table__

# role_permissions -> permissions: the join both statements share.
_GRANT_JOIN = _ROLE_PERMISSIONS.join(_PERMISSIONS, _PERMISSIONS.c.id == _ROLE_PERMISSIONS.c.permission_id)


def user_permission_names_query(user_id: uuid.UUID) -> Select:
    """Distinct permission names granted to *user_id* through all of their roles.

    One statement: user_roles -> role_permissions -> permissions.
    """
    joined = _USER_ROLES.join(_GRANT_JOIN, _ROLE_PERMISSIONS.c.role_id == _USER_ROLES.c.role_id)
    return select(_PERMISSIONS.c.name).select_from(joined).where(_USER_ROLES.c.user_id == user_id).distinct()


def role_grant_rows_query() -> Select:
    """Every (role, permission) grant, with how many users hold the role.

    Columns: role_id, role_name, org_id, is_system, permission_name, users_holding.
    Ordered by role name, then permission name, so the report is stable.
    """
    holders = (
        select(_USER_ROLES.c.role_id, func.count().label("users_holding"))
        .group_by(_USER_ROLES.c.role_id)
        .subquery("holders")
    )
    joined = _ROLES.join(_GRANT_JOIN, _ROLE_PERMISSIONS.c.role_id == _ROLES.c.id).outerjoin(
        holders, holders.c.role_id == _ROLES.c.id
    )
    return (
        select(
            _ROLES.c.id.label("role_id"),
            _ROLES.c.name.label("role_name"),
            _ROLES.c.org_id,
            _ROLES.c.is_system,
            _PERMISSIONS.c.name.label("permission_name"),
            func.coalesce(holders.c.users_holding, 0).label("users_holding"),
        )
        .select_from(joined)
        .order_by(_ROLES.c.name, _ROLES.c.id, _PERMISSIONS.c.name)
    )
