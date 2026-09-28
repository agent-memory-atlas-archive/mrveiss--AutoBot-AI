# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The workspace sweep frees slots itself and asks a human before removing anything (#16818, #17038).

Two properties carry the design, and both fail loudly here if merged back together:

* reclaiming is committed before any directory is touched, so a refused disposal never
  costs the capacity that was the point of reclaiming; and
* **no directory is removed without an approved proposal**, and an approved proposal's
  evidence is re-proved at execution rather than trusted.
"""

import importlib
import uuid
from types import SimpleNamespace

import pytest

from llc.services.workspace_disposal import DisposalCheck, DisposalVerdict

COMPANY = uuid.uuid4()


@pytest.fixture
def sweep():
    return importlib.reload(importlib.import_module("llc.scheduler.workspace_lease_sweep"))


class _Session:
    def __init__(self, approvals=None):
        self.committed = False
        self._approvals = approvals or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, statement):
        # The fake HONOURS a SQL LIMIT. Without this it silently ignores one, and a test
        # asserting "the backlog does not hide the unexecuted proposal" passes whether or
        # not the LIMIT is there -- a test that cannot fail, which is the exact defect
        # this suite exists to catch one level up.
        rows = self._approvals
        limit = getattr(statement, "_limit", None)
        if limit is None:
            limit_clause = getattr(statement, "_limit_clause", None)
            limit = getattr(limit_clause, "value", None)
        if limit is not None:
            rows = rows[:limit]

        class _R:
            def scalars(self):
                return self

            def all(self):
                return rows

        return _R()

    async def commit(self):
        self.committed = True


def _lease(path="/w/merged", branch="issue-1"):
    return SimpleNamespace(path=path, branch=branch, company_id=COMPANY)


def _approval(paths, executed=False):
    context = {
        "kind": "workspace_disposal",
        "workspaces": [{"path": p, "branch": "b"} for p in paths],
    }
    if executed:
        context["executed_at"] = "2026-09-28T00:00:00+00:00"
    return SimpleNamespace(context=context)


def _install(sweep, monkeypatch, *, reclaimed=(), landed=None, approvals=(), disposals=None):
    session = _Session(list(approvals))
    monkeypatch.setattr(sweep, "get_async_session_factory", lambda: (lambda: session))

    async def _reclaim(_s, now=None):
        return list(reclaimed)

    async def _landed(path, branch=None):
        return (landed or {}).get(path, DisposalCheck(DisposalVerdict.LANDED, "on a remote"))

    async def _dispose(path, branch):
        assert session.committed is False, "disposal runs inside the sweep transaction"
        return (disposals or {}).get(path, DisposalCheck(DisposalVerdict.LANDED, "on a remote"))

    requested = []

    class _Approvals:
        async def request_approval(self, _s, **kwargs):
            requested.append(kwargs)
            return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(sweep, "reclaim_expired", _reclaim)
    monkeypatch.setattr(sweep, "work_landed", _landed)
    monkeypatch.setattr(sweep, "dispose_workspace", _dispose)
    monkeypatch.setattr(sweep, "ApprovalService", _Approvals)
    return session, requested


# ---------------------------------------------------------------------------
# #17038: propose, never remove unattended
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_landed_workspace_is_proposed_for_approval_and_not_removed(sweep, monkeypatch):
    """The owner's ruling, as the sweep's central assertion.

    Mutation-checked: restore the old behaviour -- call `dispose_workspace` on a freshly
    reclaimed lease -- and this fails, because nothing may be disposed without an
    approval that already exists.
    """
    disposed_paths = []

    async def _never(path, branch):
        disposed_paths.append(path)
        return DisposalCheck(DisposalVerdict.LANDED, "on a remote")

    session, requested = _install(sweep, monkeypatch, reclaimed=[_lease()])
    monkeypatch.setattr(sweep, "dispose_workspace", _never)

    result = await sweep._async_sweep()

    assert result == {"reclaimed": 1, "disposed": 0, "refused": 0, "proposed": 1}
    assert disposed_paths == [], "nothing may be removed without an approved proposal"
    assert len(requested) == 1, "a proposal must be raised"


@pytest.mark.asyncio
async def test_the_proposal_carries_the_evidence_not_just_the_path(sweep, monkeypatch):
    """A reviewer approving a deletion needs the reason it is safe on the record they
    approve, not in a worker's log that they cannot see."""
    session, requested = _install(
        sweep,
        monkeypatch,
        reclaimed=[_lease()],
        landed={"/w/merged": DisposalCheck(DisposalVerdict.LANDED, "branch issue-1 is fully present on a remote")},
    )

    await sweep._async_sweep()

    payload = requested[0]["payload"]
    assert payload["kind"] == "workspace_disposal"
    entry = payload["workspaces"][0]
    assert entry["path"] == "/w/merged"
    assert "remote" in entry["evidence"], "the landedness proof must travel with the proposal"
    assert requested[0]["requested_by"] == sweep.SWEEP_REQUESTER


@pytest.mark.asyncio
async def test_a_workspace_that_did_not_land_is_never_proposed(sweep, monkeypatch):
    """The slot still comes back; the directory is not even offered for deletion."""
    session, requested = _install(
        sweep,
        monkeypatch,
        reclaimed=[_lease(path="/w/unpushed")],
        landed={"/w/unpushed": DisposalCheck(DisposalVerdict.NOT_LANDED, "2 commits on no remote")},
    )

    result = await sweep._async_sweep()

    assert result["proposed"] == 0
    assert requested == [], "a workspace with unpushed work must not reach a reviewer as disposable"
    assert result["reclaimed"] == 1 and session.committed, "the slot is freed regardless"


@pytest.mark.asyncio
async def test_an_unknown_verdict_is_not_proposed_either(sweep, monkeypatch):
    _, requested = _install(
        sweep,
        monkeypatch,
        reclaimed=[_lease(path="/w/broken")],
        landed={"/w/broken": DisposalCheck(DisposalVerdict.UNKNOWN, "git failed")},
    )
    assert (await sweep._async_sweep())["proposed"] == 0
    assert requested == []


# ---------------------------------------------------------------------------
# Executing what a human approved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_approved_proposal_is_executed(sweep, monkeypatch):
    _, _ = _install(sweep, monkeypatch, approvals=[_approval(["/w/ok"])])

    result = await sweep._async_sweep()

    assert result["disposed"] == 1 and result["refused"] == 0


@pytest.mark.asyncio
async def test_landedness_is_proved_again_at_execution_not_trusted_from_the_proposal(sweep, monkeypatch):
    """The gap approval opens.

    A proposal approved hours ago describes the directory as it was hours ago. Someone may
    have worked in it since. The approval authorises the removal; it does not vouch for
    the evidence, so the check runs again and can still refuse.
    """
    approval = _approval(["/w/changed"])
    _, _ = _install(
        sweep,
        monkeypatch,
        approvals=[approval],
        disposals={"/w/changed": DisposalCheck(DisposalVerdict.NOT_LANDED, "1 uncommitted change")},
    )

    result = await sweep._async_sweep()

    assert result["disposed"] == 0 and result["refused"] == 1
    outcome = approval.context["execution_outcomes"][0]
    assert outcome["verdict"] == "not_landed", "the refusal must be recorded on the approval"


@pytest.mark.asyncio
async def test_an_executed_proposal_is_not_executed_again(sweep, monkeypatch):
    """`executed_at` is what stops a second sweep re-running a disposal on a path that may
    have been re-leased since."""
    _, _ = _install(sweep, monkeypatch, approvals=[_approval(["/w/ok"], executed=True)])

    assert (await sweep._async_sweep())["disposed"] == 0


@pytest.mark.asyncio
async def test_execution_stamps_the_approval_with_what_happened(sweep, monkeypatch):
    approval = _approval(["/w/ok"])
    _install(sweep, monkeypatch, approvals=[approval])

    await sweep._async_sweep()

    assert approval.context["executed_at"], "the decision and its outcome live on one record"
    assert approval.context["execution_outcomes"][0]["verdict"] == "landed"


# ---------------------------------------------------------------------------
# Which company a proposal is filed under
# ---------------------------------------------------------------------------


def test_a_single_company_batch_is_filed_under_that_company(sweep):
    cid = str(uuid.uuid4())
    assert str(sweep.proposed_company([{"company_id": cid}, {"company_id": cid}])) == cid


def test_a_mixed_batch_is_filed_with_no_company_rather_than_a_guessed_one(sweep):
    """Guessing would put one company's directories in front of another's reviewer."""
    entries = [{"company_id": str(uuid.uuid4())}, {"company_id": str(uuid.uuid4())}]
    assert sweep.proposed_company(entries) is None


@pytest.mark.asyncio
async def test_a_sweep_that_found_nothing_still_reports_zeros(sweep, monkeypatch):
    _install(sweep, monkeypatch)
    assert (await sweep._async_sweep()) == {"reclaimed": 0, "disposed": 0, "refused": 0, "proposed": 0}


# ---------------------------------------------------------------------------
# The bound must cap git operations, not rows (87's finding on #17725)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_backlog_of_executed_proposals_does_not_hide_the_unexecuted_one(sweep, monkeypatch):
    """The failure that arrives after twenty disposals and arrives silently.

    An executed proposal keeps `status = APPROVED` — execution lives in
    `context["executed_at"]`, because the approval vocabulary is six values shared by
    every gate. So executed rows go on matching the query for ever. With a SQL LIMIT
    ahead of the Python filter, once MAX_EXECUTIONS_PER_SWEEP executed proposals exist
    the limit fills entirely with them, the filter drops all of them, and no approved
    disposal is ever executed again — while reporting `disposed 0, refused 0`, which is
    exactly what a sweep with nothing to do reports.

    Twenty executed and one not. The one must be acted on.
    """
    backlog = [_approval([f"/w/done-{i}"], executed=True) for i in range(sweep.MAX_EXECUTIONS_PER_SWEEP)]
    live = _approval(["/w/waiting"])
    disposed_paths = []

    async def _dispose(path, branch):
        disposed_paths.append(path)
        return DisposalCheck(DisposalVerdict.LANDED, "on a remote")

    _install(sweep, monkeypatch, approvals=[*backlog, live])
    monkeypatch.setattr(sweep, "dispose_workspace", _dispose)

    result = await sweep._async_sweep()

    assert disposed_paths == ["/w/waiting"], "the unexecuted proposal must be reached past the backlog"
    assert result["disposed"] == 1


@pytest.mark.asyncio
async def test_the_bound_still_caps_git_operations_per_tick(sweep, monkeypatch):
    """The cap's actual job, kept: unexecuted proposals beyond the bound wait for the
    next tick rather than turning one beat into an unbounded run of git operations."""
    pending = [_approval([f"/w/p-{i}"]) for i in range(sweep.MAX_EXECUTIONS_PER_SWEEP + 5)]
    _install(sweep, monkeypatch, approvals=pending)

    result = await sweep._async_sweep()

    assert result["disposed"] == sweep.MAX_EXECUTIONS_PER_SWEEP
