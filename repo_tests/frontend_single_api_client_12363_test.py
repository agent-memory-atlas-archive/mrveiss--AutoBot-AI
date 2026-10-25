# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Each GUI talks HTTP through ONE client, ``src/utils/ApiClient.ts`` (#12363, #13916).

**The boundary.** Per app (``autobot-frontend``, ``autobot-slm-frontend``), the count is every
HTTP transport that is not the app's ``ApiClient.ts``, in a ``.ts``/``.js``/``.vue`` file under
``src``: a raw ``fetch(`` / ``window.fetch(`` / ``globalThis.fetch(`` call, an ``axios`` import
or require, and a ``new XMLHttpRequest(``. Tests, specs, stories, ``__tests__/``, ``e2e/`` and
the msw mocks under ``src/test/mocks`` mock fetch on purpose and are not read; neither are
comment lines. ``fetchWithAuth.ts`` IS counted: by its own header it is a bridge for existing
raw sites, and ``ApiClient.rawRequest()`` is the canonical path for new code.

**Where the canonical client lives (#13916 AC4).** Need to call the backend? Import ``ApiClient``
from ``@/utils/ApiClient`` (both apps); for a non-JSON response use ``ApiClient.rawRequest()``.

**One number per app, exact and down-only.** A per-file table would list frontend paths in a
``repo_tests`` module and make ``python_filter_covers_its_guards_test.py`` run the python suite
on every frontend change (see ``_hardcoded_colour_baseline.py``). Each #12363 phase-2 PR lowers
its app's pin by exactly what it removed; the pin may never rise against the base (#19283).
"""

from __future__ import annotations

import re
from pathlib import Path

from repo_tests._paths import repo_root
from repo_tests._ratchet_base import base_table
from repo_tests._reach import declare

_THIS = "repo_tests/frontend_single_api_client_12363_test.py"
_CANONICAL = "src/utils/ApiClient.ts"
_SUFFIXES = (".ts", ".js", ".vue")
_EXCLUDED = re.compile(r"(\.test\.|\.spec\.|\.stories\.|/__tests__/|/e2e/|/src/test/mocks/)")
_TRANSPORT = re.compile(
    r"(?:(?<![\w.])|\bwindow\.|\bglobalThis\.)fetch\s*\("
    r"|\bfrom\s+['\"]axios['\"]|\brequire\(\s*['\"]axios['\"]"
    r"|\bnew\s+XMLHttpRequest\s*\("
)
_COMMENT = re.compile(r"^\s*(//|\*|/\*|<!--)")
#: The existing per-site exemption (eslint's raw-fetch ban): it must follow a comment giving the reason.
_EXEMPT = re.compile(r"eslint-disable-next-line\s+no-restricted-syntax")

# Measured on b07673947b; the SLM's Prometheus/Grafana probes are eslint-exempted with a reason. Lower an app's pin by exactly what a PR removes (#12363 phase 2).
PINS = {"autobot-frontend": 14, "autobot-slm-frontend": 1}


def _sources(root: Path) -> list[Path]:
    """Every non-canonical, non-test source file of both GUIs."""
    found = []
    for app in PINS:
        src = root / app / "src"
        if not src.is_dir():
            continue
        for path in sorted(src.rglob("*")):
            rel = path.relative_to(root / app).as_posix()
            if path.suffix in _SUFFIXES and rel != _CANONICAL and not _EXCLUDED.search("/" + rel):
                found.append(path)
    return found


#: 1,088 files measured on b07673947b across both GUIs; repinned by scripts/repin_reach_floors.py.
REACH = declare(
    "frontend-http-transports",
    discover=_sources,
    floor=1088,  # mid-window: population 1088, window (1088, 1088)
    what="non-test GUI source files",
    roots=tuple(f"{app}/src" for app in PINS),
)


def _transports(text: str) -> int:
    """Transports in one file; one under a reasoned ``eslint-disable-next-line no-restricted-syntax`` is exempt."""
    lines, total = text.splitlines(), 0
    for i, line in enumerate(lines):
        if _COMMENT.match(line) or not _TRANSPORT.search(line):
            continue
        if i >= 2 and _EXEMPT.search(lines[i - 1]) and _COMMENT.match(lines[i - 2]):
            continue
        total += len(_TRANSPORT.findall(line))
    return total


def count_transports(app: Path, files: list[Path] | None = None) -> int:
    """Non-canonical HTTP transports under *app*/src (from *files* when the caller already enumerated them)."""
    if files is None:
        files = _sources(app.parent) if app.name in PINS else [p for p in (app / "src").rglob("*") if p.is_file()]
    total = 0
    for path in files:
        rel = path.relative_to(app).as_posix() if path.is_relative_to(app) else None
        if rel is None or path.suffix not in _SUFFIXES or rel == _CANONICAL or _EXCLUDED.search("/" + rel):
            continue
        total += _transports(path.read_text(encoding="utf-8"))
    return total


class TestTheDetectorWorksBeforeItsOutputIsRead:
    def _w(self, app: Path, rel: str, body: str) -> None:
        target = app / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def test_it_counts_each_transport_shape(self, tmp_path: Path) -> None:
        self._w(tmp_path, "src/views/A.vue", "<script setup>\nconst r = await fetch(url)\n</script>\n")
        self._w(tmp_path, "src/b.ts", "import axios from 'axios'\nwindow.fetch(u); globalThis.fetch(v)\n")
        self._w(tmp_path, "src/c.js", "const ax = require('axios')\nconst x = new XMLHttpRequest()\n")
        assert count_transports(tmp_path) == 6

    def test_it_skips_the_client_tests_mocks_stories_and_comments(self, tmp_path: Path) -> None:
        self._w(tmp_path, _CANONICAL, "const response = await fetch(url, fetchOptions)\n")
        for rel in ("src/a.test.ts", "src/a.spec.ts", "src/a.stories.ts", "src/__tests__/b.ts", "src/e2e/c.ts"):
            self._w(tmp_path, rel, "fetch(u)\n")
        self._w(tmp_path, "src/test/mocks/api-handlers.ts", "fetch(u)\n")
        self._w(tmp_path, "src/d.ts", "// was fetch(u) before #12363\n * fetch(v)\n")
        self._w(tmp_path, "src/E.vue", "<!-- #12105: the gallery owns its own API fetch (useApi) -->\n")
        assert count_transports(tmp_path) == 0

    def test_a_reasoned_eslint_exemption_is_not_counted_and_a_bare_one_is(self, tmp_path: Path) -> None:
        reasoned = "// Prometheus is another service on its own origin.\n// eslint-disable-next-line no-restricted-syntax\nawait fetch(p)\n"
        bare = "const x = 1\n// eslint-disable-next-line no-restricted-syntax\nawait fetch(q)\n"
        self._w(tmp_path, "src/A.vue", reasoned)
        self._w(tmp_path, "src/B.vue", bare)
        assert count_transports(tmp_path) == 1

    def test_a_method_named_fetch_is_not_a_transport(self, tmp_path: Path) -> None:
        self._w(tmp_path, "src/a.ts", "store.fetch(id)\nawait this.fetch()\nprefetch(x)\nrefetch()\n")
        assert count_transports(tmp_path) == 0


def test_each_gui_has_no_more_transports_than_pinned() -> None:
    root = repo_root()
    files = REACH.examined(root)
    for app, pinned in PINS.items():
        assert (root / app / _CANONICAL).is_file(), f"{app}/{_CANONICAL} has moved; this guard reads it by path"
        now = count_transports(root / app, files)
        assert now <= pinned, (
            f"{app}: {now} HTTP transports outside {_CANONICAL}, pin allows {pinned} (#12363). "
            "Call the backend through ApiClient (ApiClient.rawRequest() for a non-JSON response)."
        )
        assert now == pinned, f"{app}: {now} transports, pin says {pinned}: lower PINS to {now} in this PR (#12363)."
    REACH.completed(files)


def test_no_pin_was_raised_against_the_base() -> None:
    base = base_table(repo_root(), (_THIS,), "PINS")
    if not base:
        return  # the guard is new at this base; nothing to compare against yet
    raised = {app: (base[app], pin) for app, pin in PINS.items() if app in base and pin > base[app]}
    assert not raised, f"pins rose against the base (base, now): {raised}; a pin only goes down (#12363)."
