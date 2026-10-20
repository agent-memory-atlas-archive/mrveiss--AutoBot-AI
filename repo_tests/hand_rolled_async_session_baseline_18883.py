# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Frozen allowlist of the hand-rolled fake async sessions that exist today (#18883).

Each key is `<repo path>::<enclosing function>::<class>` (`<module>` at module level); the value
is why it is still hand-rolled. The list may only SHRINK: a new match fails the guard, and an
entry whose class was migrated to `testkit.fake_async_session.FakeAsyncSession` (or left the
tree) fails it too until the line is deleted. `FROZEN_TOTAL` caps the length.
"""

from __future__ import annotations

_B = "autobot-backend/"
_S = "autobot-slm-backend/"

ALLOWLIST: dict[str, str] = {
    _B
    + "llc/tests/test_status_comment_is_stored_16017.py::test_a_failed_commit_leaves_no_comment_behind::_FailingCommitSession": (  # noqa: E501
        "KEEP-EXCEPTION: proxy over a real aiosqlite session, not a stand-in for one"
    ),
}

#: The ceiling. Lower it with the list; never raise it.
FROZEN_TOTAL = 1
