# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The workspace sweep frees slots first and removes directories only on evidence (#16818).

The ordering is the whole design: reclaim is committed before any directory is touched,
so a disposal that fails, refuses or crashes never costs the capacity that was the point
of reclaiming. These tests fail if the two are ever merged back into one step.
"""

import importlib
from types import SimpleNamespace

import pytest

from llc.services.workspace_disposal import DisposalCheck, DisposalVerdict


@pytest.fixture
def sweep():
    return importlib.import_module("llc.scheduler.workspace_lease_sweep")


class _Session:
    def __init__(self):
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def commit(self):
        self.committed = True


def _install(sweep, monkeypatch, reclaimed, disposals):
    session = _Session()
    monkeypatch.setattr(sweep, "get_async_session_factory", lambda: (lambda: session))

    async def _reclaim(_session, now=None):
        assert not session.committed, "the reclaim must run before the commit, not after"
        return reclaimed

    seen = []

    async def _dispose(path, branch):
        assert session.committed, "no directory may be touched before the reclaim is committed"
        seen.append(path)
        return disposals[path]

    monkeypatch.setattr(sweep, "reclaim_expired", _reclaim)
    monkeypatch.setattr(sweep, "dispose_workspace", _dispose)
    return session, seen


@pytest.mark.asyncio
async def test_the_sweep_reclaims_then_disposes_of_what_landed(sweep, monkeypatch):
    """AC6's first half end to end: an expired lease is reclaimed and its directory disposed."""
    lease = SimpleNamespace(path="/w/merged", branch="issue-1")
    session, seen = _install(
        sweep,
        monkeypatch,
        [lease],
        {"/w/merged": DisposalCheck(DisposalVerdict.LANDED, "on a remote")},
    )

    result = await sweep._async_sweep()

    assert result == {"reclaimed": 1, "disposed": 1, "kept": 0}
    assert seen == ["/w/merged"]
    assert session.committed


@pytest.mark.asyncio
async def test_a_workspace_with_unpushed_work_is_kept_and_its_slot_still_freed(sweep, monkeypatch):
    """The case that decides whether this is an improvement or a new way to lose work.

    The lease comes back -- capacity is restored -- and the directory stays. Anything
    that ties the two together is either the old deadlock or data loss.
    """
    lease = SimpleNamespace(path="/w/unpushed", branch="issue-2")
    session, seen = _install(
        sweep,
        monkeypatch,
        [lease],
        {"/w/unpushed": DisposalCheck(DisposalVerdict.NOT_LANDED, "2 commits on no remote")},
    )

    result = await sweep._async_sweep()

    assert result == {"reclaimed": 1, "disposed": 0, "kept": 1}
    assert session.committed, "the slot is freed whether or not the directory could go"


@pytest.mark.asyncio
async def test_an_unknown_verdict_keeps_the_workspace_rather_than_disposing_of_it(sweep, monkeypatch):
    """UNKNOWN is counted with the refusals, never with the removals."""
    lease = SimpleNamespace(path="/w/broken", branch=None)
    _install(
        sweep,
        monkeypatch,
        [lease],
        {"/w/broken": DisposalCheck(DisposalVerdict.UNKNOWN, "git failed")},
    )

    assert (await sweep._async_sweep()) == {"reclaimed": 1, "disposed": 0, "kept": 1}


@pytest.mark.asyncio
async def test_a_sweep_that_found_nothing_still_reports_zeros(sweep, monkeypatch):
    """#16817's lesson, kept here: nothing found and did not run must not look alike."""
    _install(sweep, monkeypatch, [], {})
    assert (await sweep._async_sweep()) == {"reclaimed": 0, "disposed": 0, "kept": 0}
