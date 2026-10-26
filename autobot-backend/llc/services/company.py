# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""CompanyService — LLC lifecycle management for Organization entities (GH#8211).

Extends LLCServiceBase with CRUD operations, sub-company tree traversal,
ancestry lookup, budget enforcement, and status lifecycle transitions
(suspend / archive).
"""

import asyncio
import uuid
from typing import List, Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from autobot_shared.db_errors import PG_DEADLOCK_DETECTED, sqlstate_of
from autobot_shared.logging_manager import get_logger
from autobot_shared.time_utils import now_utc
from llc.models.company import CompanyCreate, CompanyTreeNode, CompanyUpdate
from llc.models.contact import LLCContact
from llc.models.enums import LLCCompanyStatus
from user_management.models.organization import Organization

from .authz import (
    NotAuthorisedError,
    may_attach_under,
    require_company_admin,
)
from .base import LLCServiceBase
from .company_ceo import CompanyCEOService

logger = get_logger(__name__)

#: Most chain-lock passes one move takes before giving up (#19593). A pass repeats
#: only when a concurrent move committed a change to the new parent's ancestor chain
#: between reading and locking it; needing more than this means the tree is being
#: churned under the move, and refusing it is safer than looping.
MOVE_LOCK_MAX_PASSES = 8


class CompanyNotFoundError(Exception):
    """Raised when the requested company does not exist."""


class CompanyCycleError(Exception):
    """Raised when a parent_org_id assignment would create a hierarchy cycle."""


class CompanyMoveContendedError(Exception):
    """A move lost a lock race; nothing was changed and the caller should retry (HTTP 409).

    Raised when the ancestor chain kept changing for MOVE_LOCK_MAX_PASSES passes, or
    when Postgres picked this move as a deadlock victim. Either way the transaction
    must be rolled back and the move retried from the start: only a fresh
    transaction gives up the rows this one already holds.
    """


class CompanyBudgetError(Exception):
    """Raised when a child company budget would exceed parent remaining budget."""


class CompanyIssuePrefixConflictError(Exception):
    """Raised when the requested issue_prefix is already taken."""


class CompanyHasChildrenError(Exception):
    """Raised when attempting to delete a company that still has active child companies."""


class CompanyService(LLCServiceBase):
    """LLC service for company CRUD + lifecycle operations.

    All methods accept an ``AsyncSession`` so they can participate in the
    caller's transaction.  Callers are responsible for ``session.commit()``.
    """

    def __init__(self, session: AsyncSession, **kwargs) -> None:
        super().__init__(**kwargs)
        self.session = session

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(self, data: CompanyCreate) -> Organization:
        """Create a new company (Organization with LLC fields)."""
        await self._assert_prefix_unique(data.issue_prefix)

        if data.parent_org_id is not None:
            parent = await self._get_or_404(data.parent_org_id)
            await self._assert_budget_fits(parent, data.budget_monthly_cents)

        org = Organization(
            name=data.name,
            slug=data.slug,
            description=data.description,
            settings={},
            parent_org_id=data.parent_org_id,
            issue_prefix=data.issue_prefix,
            budget_monthly_cents=data.budget_monthly_cents,
            brand_color=data.brand_color,
            require_approval_for_hires=data.require_approval_for_hires,
            llc_status=data.llc_status.value,
        )
        self.session.add(org)
        await self.session.flush()

        await self._place_in_hierarchy(org)

        logger.info("LLC company created: %s (id=%s)", org.name, org.id)
        return org

    async def _place_in_hierarchy(self, org: Organization) -> None:
        """Cycle-check a sub-organization; give a company -- and only a company -- its CEO.

        #15770: a company with no CEO is a disconnected forest, so it is provisioned
        here rather than lazily on first read. #15907: a sub-organization gets none
        (migration 20260907_090, #15892). A soft-deleted org never reaches this:
        ``deleted_at`` is unset on a row this transaction has just inserted.
        """
        if org.parent_org_id is not None:
            await self._assert_no_cycle(org.id, org.parent_org_id)
        await CompanyCEOService().sync_with_parent(self.session, org.id, org.parent_org_id)

    async def get(self, company_id: uuid.UUID) -> Organization:
        """Return the company or raise CompanyNotFoundError."""
        return await self._get_or_404(company_id)

    async def update(
        self,
        company_id: uuid.UUID,
        data: CompanyUpdate,
        *,
        actor_user_id: Optional[uuid.UUID] = None,
        platform_admin: bool = False,
    ) -> Organization:
        """Apply a partial update; a changed ``parent_org_id`` goes through :meth:`move`.

        ``parent_org_id`` alone distinguishes absent from null (#19589): an explicit
        ``null`` promotes to top level, an omitted field leaves the parent alone.
        Every other field keeps "``None`` means unchanged".
        """
        org = await self._apply_parent_change(await self._get_or_404(company_id), data, actor_user_id, platform_admin)
        await self._assert_fields_fit(org, data)
        for field, value in data.model_dump(exclude_none=True, exclude={"parent_org_id"}).items():
            setattr(org, field, value.value if isinstance(value, LLCCompanyStatus) else value)
        # #12322: ``updated_at`` comes back inline from the UPDATE's RETURNING
        # (``eager_defaults=True``), so the router's sync serialization is safe.
        await self.session.flush()
        logger.info("LLC company updated: %s (id=%s)", org.name, org.id)
        return org

    async def _apply_parent_change(
        self,
        org: Organization,
        data: CompanyUpdate,
        actor_user_id: Optional[uuid.UUID],
        platform_admin: bool,
    ) -> Organization:
        """Move first, so a caller without move rights gets 403 before any field check (#19634)."""
        if "parent_org_id" not in data.model_fields_set or data.parent_org_id == org.parent_org_id:
            return org
        return await self.move(org.id, data.parent_org_id, actor_user_id=actor_user_id, platform_admin=platform_admin)

    async def _assert_fields_fit(self, org: Organization, data: CompanyUpdate) -> None:
        """Prefix uniqueness, and the budget against the parent the org now has (after any move)."""
        if data.issue_prefix is not None and data.issue_prefix != org.issue_prefix:
            await self._assert_prefix_unique(data.issue_prefix)
        if data.budget_monthly_cents is not None and org.parent_org_id is not None:
            parent = await self._get_or_404(org.parent_org_id)
            await self._assert_budget_fits(parent, data.budget_monthly_cents, exclude_id=org.id)

    async def move(
        self,
        company_id: uuid.UUID,
        parent_org_id: Optional[uuid.UUID],
        *,
        actor_user_id: Optional[uuid.UUID],
        platform_admin: bool = False,
    ) -> Organization:
        """Re-parent *company_id* (``None`` = top level); the CEO follows the move (#15907).

        Authorized here so every caller inherits it (#19634): an org with a parent is
        moved by an owner/admin of that parent, a top-level org by its own owner/admin,
        and a new parent needs owner/admin there (else not found); a platform admin
        passes every check. The moved org and the new parent's ancestor chain are
        locked first (#19593), so the cycle check and the CEO sync read a tree no
        concurrent move can change before this transaction ends.
        """
        await self._authorize_move_target(company_id, parent_org_id, actor_user_id, platform_admin)
        await self._lock_move_rows(company_id, parent_org_id)
        # Already locked above; ``for_update`` is a no-op relock kept for its
        # ``populate_existing``, which replaces any pre-lock copy in the identity map.
        org = await self._get_or_404(company_id, for_update=True)
        await self._authorize_locked_move(org, parent_org_id, actor_user_id, platform_admin)
        if parent_org_id is not None:
            await self._get_or_404(parent_org_id)
            await self._assert_no_cycle(company_id, parent_org_id)
        was_company = org.parent_org_id is None
        org.parent_org_id = parent_org_id
        await self.session.flush()
        if (parent_org_id is None) != was_company:
            await CompanyCEOService().sync_with_parent(self.session, org.id, parent_org_id)
        return org

    async def _authorize_move_target(
        self,
        company_id: uuid.UUID,
        parent_org_id: Optional[uuid.UUID],
        actor_user_id: Optional[uuid.UUID],
        platform_admin: bool,
    ) -> None:
        """Before any lock: the move's authority, then the new parent (#19634, #12233's rule).

        Checked first so a caller with no rights here never takes a row lock. The
        org is read unlocked; :meth:`_authorize_locked_move` repeats the authority
        check once the row is locked and its current parent is certain.
        """
        org = await self._get_or_404(company_id)
        await self._require_move_authority(org, parent_org_id, actor_user_id, platform_admin, for_update=False)
        if parent_org_id is None:
            return
        if not await may_attach_under(self.session, parent_org_id, actor_user_id, platform_admin=platform_admin):
            raise CompanyNotFoundError(f"Company {parent_org_id} not found")

    async def _authorize_locked_move(
        self,
        org: Organization,
        parent_org_id: Optional[uuid.UUID],
        actor_user_id: Optional[uuid.UUID],
        platform_admin: bool,
    ) -> None:
        """Under the lock: the authority again, with its membership row locked ``FOR UPDATE``.

        A revoke or role change of the deciding membership then waits for this move
        instead of racing it.
        """
        await self._require_move_authority(org, parent_org_id, actor_user_id, platform_admin, for_update=True)

    async def _require_move_authority(
        self,
        org: Organization,
        parent_org_id: Optional[uuid.UUID],
        actor_user_id: Optional[uuid.UUID],
        platform_admin: bool,
        *,
        for_update: bool,
    ) -> None:
        """Who decides a move (maintainer ruling, 2026-10-26).

        A platform admin always may. An org WITH a parent is moved from the parent
        org (promotion or sideways): an owner/admin of that parent decides, with no
        role needed in the sub-org; the sub-org's own owner may not. A top-level org
        decides itself to join a parent: owner/admin of the moved org.
        """
        if platform_admin:
            return
        if org.parent_org_id is None or org.parent_org_id == parent_org_id:
            await require_company_admin(self.session, org.id, actor_user_id, for_update=for_update)
            return
        try:
            await require_company_admin(self.session, org.parent_org_id, actor_user_id, for_update=for_update)
        except NotAuthorisedError as exc:
            raise NotAuthorisedError(
                f"moving company {org.id} out of its parent must be done by an owner/admin of the parent "
                f"org {org.parent_org_id}"
            ) from exc

    async def _lock_move_rows(self, company_id: uuid.UUID, parent_org_id: Optional[uuid.UUID]) -> None:
        """Lock the moved org and the new parent's ancestor chain ``FOR UPDATE``, in id order (#19593).

        Overlapping movers each take their set in one id-ordered statement, so one
        waits for the other rather than both reading a tree about to change. The chain
        is re-read under the lock; if it grew (a concurrent move committed), the next
        pass re-locks the FULL set in id order, up to MOVE_LOCK_MAX_PASSES, then
        :class:`CompanyMoveContendedError`. A later pass still holds earlier rows while
        it waits, so a wait cycle is possible there; Postgres then aborts one mover with
        ``deadlock_detected``, which :meth:`_lock_ids` turns into the same retryable
        error. An in-transaction (savepoint) retry would rebuild the same cycle, so the
        retry is a fresh transaction by the caller. Fails closed: never a cycle.
        """
        wanted = {company_id, *await self._chain_ids(parent_org_id)}
        for _ in range(MOVE_LOCK_MAX_PASSES):
            await self._lock_ids(wanted)
            chain = {company_id, *await self._chain_ids(parent_org_id)}
            if chain <= wanted:
                return
            wanted |= chain
        raise CompanyMoveContendedError(
            f"Moving company {company_id}: the new parent's ancestor chain kept changing "
            f"across {MOVE_LOCK_MAX_PASSES} lock passes; retry the move"
        )

    async def _chain_ids(self, start_id: Optional[uuid.UUID]) -> List[uuid.UUID]:
        """*start_id* and its ancestors, nearest first; :class:`CompanyCycleError` on a loop."""
        chain: List[uuid.UUID] = []
        current_id = start_id
        while current_id is not None:
            if current_id in chain:
                raise CompanyCycleError(f"Company {start_id} has a hierarchy cycle above it")
            chain.append(current_id)
            result = await self.session.execute(select(Organization.parent_org_id).where(Organization.id == current_id))
            current_id = result.scalar_one_or_none()
        return chain

    async def _lock_ids(self, ids: set[uuid.UUID]) -> None:
        """Lock *ids* ``FOR UPDATE`` in one id-ordered statement; a deadlock is a retryable refusal."""
        stmt = select(Organization.id).where(Organization.id.in_(ids)).order_by(Organization.id).with_for_update()
        try:
            await self.session.execute(stmt)
        except DBAPIError as exc:
            if sqlstate_of(exc) != PG_DEADLOCK_DETECTED:
                raise
            logger.info("Company move deadlocked with a concurrent move; refused as retryable")
            raise CompanyMoveContendedError("a concurrent move holds rows this move needs; retry the move") from exc

    async def list_root_companies(self, include_archived: bool = False) -> List[Organization]:
        """Return all top-level companies (parent_org_id IS NULL).

        ARCHIVED companies are hidden by default (#12212) so retired/abandoned
        entries do not clutter the selector; pass ``include_archived=True`` to
        surface them again (the "show archived" toggle) so they stay recoverable.
        """
        stmt = select(Organization).where(Organization.parent_org_id.is_(None)).where(Organization.deleted_at.is_(None))
        if not include_archived:
            stmt = stmt.where(Organization.llc_status != LLCCompanyStatus.ARCHIVED.value)
        result = await self.session.execute(stmt.order_by(Organization.name))
        return list(result.scalars().all())

    async def list_children(self, parent_id: uuid.UUID) -> List[Organization]:
        """Return direct children of the given company."""
        result = await self.session.execute(
            select(Organization)
            .where(Organization.parent_org_id == parent_id)
            .where(Organization.deleted_at.is_(None))
            .order_by(Organization.name)
        )
        return list(result.scalars().all())

    async def delete(self, company_id: uuid.UUID) -> None:
        """Soft-delete a company.

        Raises CompanyHasChildrenError when active child companies exist, preventing
        orphaned children that ON DELETE SET NULL cannot handle for soft-deletes.

        #13969 review M2: the company itself is soft-deleted (``deleted_at``),
        but its contacts are pure PII with their own "deletion removes PII"
        acceptance criterion — a soft-deleted company must not leave every
        contact's name/email/phone/notes sitting at rest forever, unreachable
        by any UI path (every listing filters ``deleted_at.is_(None)``). This
        purges the company's ``llc_contacts`` rows in the same transaction,
        rather than the more complex option of reassigning them elsewhere,
        which is a product decision (tracked as #13998) this method should
        not make unilaterally.
        """
        org = await self._get_or_404(company_id)
        children = await self.list_children(company_id)
        if children:
            raise CompanyHasChildrenError(
                f"Company {company_id} has {len(children)} active child company(s); "
                "re-parent or delete children before deleting the parent"
            )
        org.soft_delete()
        await self.session.execute(sa_delete(LLCContact).where(LLCContact.company_id == company_id))
        await self.session.flush()
        logger.info("LLC company soft-deleted: %s (id=%s), its contacts purged", org.name, org.id)

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    # Valid states from which activate() is allowed (ONBOARDING -> ACTIVE, or
    # PAUSED -> ACTIVE as a resume). Issue #12211: without this, a company can
    # never leave ONBOARDING.
    _ACTIVATE_FROM: frozenset[str] = frozenset({LLCCompanyStatus.ONBOARDING.value, LLCCompanyStatus.PAUSED.value})
    # Valid states from which suspend() is allowed
    _SUSPEND_FROM: frozenset[str] = frozenset({LLCCompanyStatus.ONBOARDING.value, LLCCompanyStatus.ACTIVE.value})
    # Valid states from which offboard() is allowed. Issue #12234: OFFBOARDING
    # was defined and included in _ARCHIVE_FROM (below) but nothing ever
    # transitioned a company into it, making the state unreachable.
    _OFFBOARD_FROM: frozenset[str] = frozenset({LLCCompanyStatus.ACTIVE.value})
    # Valid states from which archive() is allowed
    _ARCHIVE_FROM: frozenset[str] = frozenset({LLCCompanyStatus.PAUSED.value, LLCCompanyStatus.OFFBOARDING.value})

    async def _transition(
        self,
        company_id: uuid.UUID,
        *,
        verb: str,
        allowed_from: frozenset[str],
        target: LLCCompanyStatus,
        log_msg: str,
        **field_updates: object,
    ) -> Organization:
        """Shared status-transition primitive (#12238): guard + set + flush + log.

        Loads the company, rejects an out-of-range current ``llc_status`` with
        the canonical ``Cannot {verb} company in '<state>' state`` ValueError,
        applies ``llc_status=target`` plus any *field_updates* (in order), then
        flushes and logs *log_msg*. The public activate/suspend/offboard/archive
        methods are thin wrappers so guard + messages live in one place.
        """
        org = await self._get_or_404(company_id)
        if org.llc_status not in allowed_from:
            raise ValueError(
                f"Cannot {verb} company in '{org.llc_status}' state "
                f"(allowed from: {', '.join(sorted(allowed_from))})"
            )
        org.llc_status = target.value
        for field, value in field_updates.items():
            setattr(org, field, value)
        await self.session.flush()
        # #12322: the onupdate `updated_at` is populated inline by the UPDATE's
        # RETURNING clause (Base sets ``eager_defaults=True``), so it is never
        # expired after flush() and the router's sync response serialization is
        # safe. The previous per-call ``session.refresh(org, ["updated_at"])``
        # (#12309) is now redundant and removed.
        logger.info(log_msg, org.name, org.id)
        return org

    async def activate(self, company_id: uuid.UUID) -> Organization:
        """Transition company to ACTIVE status.

        Allowed from ONBOARDING (finish onboarding) or PAUSED (resume) — raises
        ValueError otherwise. Clears any pause state so a resumed company is no
        longer marked paused (#12211).
        """
        return await self._transition(
            company_id,
            verb="activate",
            allowed_from=self._ACTIVATE_FROM,
            target=LLCCompanyStatus.ACTIVE,
            log_msg="LLC company activated: %s (id=%s)",
            pause_reason=None,
            paused_at=None,
        )

    async def suspend(self, company_id: uuid.UUID, reason: Optional[str] = None) -> Organization:
        """Transition company to PAUSED status.

        Only allowed from ONBOARDING or ACTIVE — raises ValueError otherwise.
        """
        return await self._transition(
            company_id,
            verb="suspend",
            allowed_from=self._SUSPEND_FROM,
            target=LLCCompanyStatus.PAUSED,
            log_msg="LLC company suspended: %s (id=%s)",
            pause_reason=reason,
            paused_at=now_utc(),
        )

    async def offboard(self, company_id: uuid.UUID) -> Organization:
        """Transition company to OFFBOARDING status.

        Only allowed from ACTIVE — raises ValueError otherwise. This is the
        step before archive() (#12234): OFFBOARDING was already a valid
        ``_ARCHIVE_FROM`` source but no transition ever set it, leaving it
        unreachable.
        """
        return await self._transition(
            company_id,
            verb="offboard",
            allowed_from=self._OFFBOARD_FROM,
            target=LLCCompanyStatus.OFFBOARDING,
            log_msg="LLC company offboarding started: %s (id=%s)",
        )

    async def archive(self, company_id: uuid.UUID) -> Organization:
        """Transition company to ARCHIVED status.

        Only allowed from PAUSED or OFFBOARDING — raises ValueError otherwise.
        """
        return await self._transition(
            company_id,
            verb="archive",
            allowed_from=self._ARCHIVE_FROM,
            target=LLCCompanyStatus.ARCHIVED,
            log_msg="LLC company archived: %s (id=%s)",
        )

    # ------------------------------------------------------------------
    # Tree / ancestry
    # ------------------------------------------------------------------

    async def get_sub_company_tree(self, org_id: uuid.UUID) -> CompanyTreeNode:
        """Return the recursive sub-company tree rooted at org_id."""
        root = await self._get_or_404(org_id)
        return await self._build_tree_node(root)

    async def get_ancestry(self, org_id: uuid.UUID) -> List[Organization]:
        """Return the chain of ancestors from root down to org_id (exclusive)."""
        org = await self._get_or_404(org_id)
        chain: List[Organization] = []
        visited: set[uuid.UUID] = {org.id}

        current_id = org.parent_org_id
        while current_id is not None:
            if current_id in visited:
                break
            parent = await self._get_or_404(current_id)
            chain.append(parent)
            visited.add(parent.id)
            current_id = parent.parent_org_id

        chain.reverse()
        return chain

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_or_404(self, company_id: uuid.UUID, for_update: bool = False) -> Organization:
        stmt = select(Organization).where(Organization.id == company_id).where(Organization.deleted_at.is_(None))
        if for_update:
            # Re-read under the lock: a row already in the identity map would otherwise
            # keep the value read before the lock was granted.
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result = await self.session.execute(stmt)
        org = result.scalar_one_or_none()
        if org is None:
            raise CompanyNotFoundError(f"Company {company_id} not found")
        return org

    async def _assert_prefix_unique(self, prefix: Optional[str]) -> None:
        if prefix is None:
            return
        result = await self.session.execute(
            select(Organization.id).where(Organization.issue_prefix == prefix).where(Organization.deleted_at.is_(None))
        )
        if result.scalar_one_or_none() is not None:
            raise CompanyIssuePrefixConflictError(f"issue_prefix '{prefix}' is already taken")

    async def _assert_budget_fits(
        self,
        parent: Organization,
        child_budget_cents: int,
        exclude_id: Optional[uuid.UUID] = None,
    ) -> None:
        """Ensure child budget does not exceed parent remaining budget.

        Pass exclude_id=company_id when updating an existing child to avoid
        double-counting that child's current allocation.
        """
        if parent.budget_monthly_cents == 0:
            return
        existing_children_total = await self._sum_children_budget(parent.id, exclude_id=exclude_id)
        remaining = parent.budget_monthly_cents - parent.spent_monthly_cents - existing_children_total
        if child_budget_cents > remaining:
            raise CompanyBudgetError(
                f"Child budget {child_budget_cents} cents exceeds parent remaining " f"budget {remaining} cents"
            )

    async def _sum_children_budget(
        self,
        parent_id: uuid.UUID,
        exclude_id: Optional[uuid.UUID] = None,
    ) -> int:
        stmt = (
            select(Organization.budget_monthly_cents)
            .where(Organization.parent_org_id == parent_id)
            .where(Organization.deleted_at.is_(None))
        )
        if exclude_id is not None:
            stmt = stmt.where(Organization.id != exclude_id)
        result = await self.session.execute(stmt)
        return sum(row for row in result.scalars().all())

    async def _build_tree_node(
        self,
        org: Organization,
        visited: Optional[set[uuid.UUID]] = None,
    ) -> CompanyTreeNode:
        if visited is None:
            visited = set()
        if org.id in visited:
            raise CompanyCycleError(f"Cycle detected in company tree at id={org.id}")
        current_visited = visited | {org.id}
        children_orgs = await self.list_children(org.id)
        children_nodes = list(await asyncio.gather(*[self._build_tree_node(c, current_visited) for c in children_orgs]))
        return CompanyTreeNode(
            id=org.id,
            name=org.name,
            slug=org.slug,
            llc_status=LLCCompanyStatus(org.llc_status),
            children=children_nodes,
        )

    async def _assert_no_cycle(self, target_id: uuid.UUID, new_parent_id: uuid.UUID) -> None:
        """Raise CompanyCycleError if setting new_parent_id would create a cycle."""
        if target_id in await self._chain_ids(new_parent_id):
            raise CompanyCycleError(f"Setting parent_org_id={new_parent_id} would create a hierarchy cycle")
