# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""One `playwright-server.js` (#18549).

The browser worker existed in three copies (`autobot-browser-worker/`, an infrastructure
scripts copy and a tests copy) that had drifted apart: only the first carried per-session
contexts and the `/automation` dispatcher. Every systemd unit, compose file and setup
script now runs or copies `autobot-browser-worker/playwright-server.js`; a second tracked
file of that name is the fork returning.

The detector is a basename comparison over the tracked JavaScript of every tree that has
ever held a copy, so a copy renamed into a new directory is still caught while a doc that
merely names the file is not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

_CANONICAL = "autobot-browser-worker/playwright-server.js"
_NAME = "playwright-server.js"
_ROOTS = ("autobot-browser-worker", "autobot-infrastructure", "autobot-frontend")


def _copies(paths: list[str]) -> list[str]:
    """Paths whose file name is the server's, whichever directory holds them."""
    return [rel for rel in paths if rel.rsplit("/", 1)[-1] == _NAME]


def _javascript_population(root: Path) -> list[str]:
    """Tracked `.js` under the roots. An empty enumeration is REACH's to refuse, not an exception."""
    try:
        tracked = tracked_paths(root, *_ROOTS)
    except EmptyEnumeration:
        return []
    return [rel for rel in tracked if rel.endswith(".js")]


REACH = declare(
    "one-playwright-server-scan",
    discover=_javascript_population,
    # Mid-window, from REACH.window() = (69, 109, 89); the arithmetic is on the PR, not here.
    floor=89,
    what="tracked javascript",
    roots=_ROOTS,
    growth=40,
)


@pytest.mark.parametrize(
    ("label", "paths", "expected"),
    [
        ("the canonical alone", [_CANONICAL, "autobot-browser-worker/session-store.js"], [_CANONICAL]),
        (
            "a deliberately added copy",
            [_CANONICAL, "autobot-infrastructure/shared/scripts/infrastructure/playwright-server.js"],
            [_CANONICAL, "autobot-infrastructure/shared/scripts/infrastructure/playwright-server.js"],
        ),
        ("a similarly named file", ["autobot-infrastructure/shared/tests/playwright-server.test.js"], []),
        ("a different name", ["autobot-frontend/server.js"], []),
    ],
)
def test_the_matcher_finds_a_copy_by_name_and_nothing_else(label: str, paths: list[str], expected: list[str]) -> None:
    assert _copies(paths) == expected, label


def test_the_scan_is_not_vacuous() -> None:
    assert _CANONICAL in REACH.examined(repo_root()), "the canonical copy must be in the scanned population"


def test_exactly_one_tracked_playwright_server() -> None:
    copies = _copies(REACH.examined(repo_root()))
    assert copies == [_CANONICAL], (
        f"#18549: tracked playwright-server.js files: {copies}; the only one is {_CANONICAL}. "
        "Point the unit, compose file or script at it instead of copying it."
    )
