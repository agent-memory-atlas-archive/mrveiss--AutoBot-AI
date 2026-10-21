# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Every ``v-html`` goes through the canonical sanitiser or is listed (#19118).

#2847 fixed stored XSS in chat and terminal with DOMPurify; KnowledgeEntries
was missed and regressed the class. A ``.vue`` file that binds ``v-html`` must
import the sanitiser (``@/utils/sanitize`` or ``dompurify``) or appear in
``UNSANITISED_ALLOWLIST`` with a reason. The allowlist is shrink-only: an
entry whose file now imports the sanitiser, or whose binding count differs
from the recorded one, fails -- delete or lower it, never raise it.
"""

from __future__ import annotations

import re
from pathlib import Path

from repo_tests._paths import repo_root

REPO = repo_root()
ROOTS = ("autobot-frontend/src", "autobot-slm-frontend/src")

# file -> (v-html binding count, reason). Shrink-only.
UNSANITISED_ALLOWLIST: dict[str, tuple[int, str]] = {
    "autobot-frontend/src/views/llc/WorkItemDetail.vue": (
        2,
        "renderMarkdown escapes & < > before adding its own tags",
    ),
    "autobot-frontend/src/components/knowledge/KBSearchResultPanel.vue": (
        2,
        "applyHighlight/highlightedContent escape & < > before wrapping <mark>",
    ),
    "autobot-frontend/src/components/canvas/CanvasCell.vue": (
        3,
        "formatMarkdown escapes & < > before adding its own tags",
    ),
    "autobot-frontend/src/components/knowledge/SystemKnowledgeManager.vue": (
        6,
        "static i18n strings with <strong>/<code>; no interpolated params",
    ),
}

_BINDING = re.compile(r"""(?<![\w-])v-html\s*=""")
_SANITISER = re.compile(r"""from\s+['"](?:@/utils/sanitize|dompurify)['"]|import\(\s*['"]dompurify['"]""")
_COMMENT_LINE = re.compile(r"^\s*(<!--|//|\*)")


def _vue_files() -> list[Path]:
    return sorted(p for root in ROOTS for p in (REPO / root).rglob("*.vue"))


def _bindings(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    return sum(len(_BINDING.findall(line)) for line in text.splitlines() if not _COMMENT_LINE.match(line))


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def test_every_v_html_is_sanitised_or_allowlisted() -> None:
    offenders = []
    for path in _vue_files():
        n = _bindings(path)
        if not n:
            continue
        rel = _rel(path)
        text = path.read_text(encoding="utf-8")
        if rel not in UNSANITISED_ALLOWLIST and not _SANITISER.search(text):
            offenders.append(f"{rel}: {n} v-html binding(s), no sanitiser import")
    assert not offenders, "route through @/utils/sanitize or allowlist with a reason:\n" + "\n".join(offenders)


def test_allowlist_is_shrink_only() -> None:
    stale = []
    for rel, (count, reason) in UNSANITISED_ALLOWLIST.items():
        path = REPO / rel
        assert reason.strip(), f"{rel}: allowlist entry needs a reason"
        if not path.exists():
            stale.append(f"{rel}: file gone, drop the entry")
            continue
        actual = _bindings(path)
        if _SANITISER.search(path.read_text(encoding="utf-8")):
            stale.append(f"{rel}: now imports the sanitiser, drop the entry")
        elif actual != count:
            stale.append(f"{rel}: {actual} binding(s) vs {count} recorded -- never raise, lower on shrink")
    assert not stale, "\n".join(stale)


def test_guard_detects_an_unsanitised_binding() -> None:
    """Control: the detector must see a bare binding and accept a sanitised one."""
    assert _BINDING.search('<div v-html="x"></div>')
    assert not _BINDING.search('<div :data-v-html="x"></div>')
    assert _SANITISER.search("import { sanitizeHtml } from '@/utils/sanitize'")
    assert not _SANITISER.search("import { escapeHtml } from '@/utils/format'")
