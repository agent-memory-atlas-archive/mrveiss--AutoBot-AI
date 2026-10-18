# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
r"""Hard-coded English UI text the frontends write instead of translating (#12732).

**The boundary.** An entry is a `.vue` file, mapped to how many hard-coded
English literals it contains, where:

* Files: `.vue` under `autobot-frontend/src` and `autobot-slm-frontend/src`,
  excluding `*.stories.*`, `__tests__/`, `*.spec.*`, `*.test.*`. Enumerated from
  the filesystem under the repo root.
* A literal is one of three kinds, detected by `repo_tests/_ui_literal_scan.py`
  (the same detector the SLM console's zero-baseline guard, #15665, uses):
  * template-text: a text node in the `<template>`, with tags and `{{ }}`
    interpolations removed, one line at a time, of at least 4 characters with two
    consecutive letters, that is not a URL, a path or a CamelCase/snake/kebab
    identifier. `<code>`, `<pre>`, `<svg>` and `<path>` content is opaque.
    A plain one-word label (`Running`, `Close`) is prose, for both guards;
  * attribute: a static (unbound) `placeholder`, `title`, `alt`, `aria-label`,
    `aria-description` or `label` whose value passes the same prose test;
  * script: in `<script>`, matching
    one of `.value =`, `message:`, `title:`, `confirm(`, `alert(`, `Error(` followed by a
    quoted string starting with a capital then a lower-case letter, closed by the SAME quote
    character it opened with (`_SCRIPT_LITERAL` in `_ui_literal_scan.py`).

**Not seen:** a literal in a custom component's own prop (`message`, `caption`,
`hint`), text built by concatenation, `v-bind` string literals, `.ts` files,
props defaults, and any language other than English.

**Pinned per file and per literal**, in
`repo_tests/hardcoded_ui_literal_baseline_12732.json` (`file -> [[kind, text], ...]`, a
multiset). A literal removed in one file cannot hide a new one in another, and swapping
one literal for another in the same file fails twice: the new one is unrecorded and the
old one is stale (#18802; a per-file count passed a swap). A removed literal is deleted
from the JSON in the commit that did the work, and a file that reaches 0 leaves it.

**Shrink-only.** Replace a literal with `t('...')` and an English key in
`en.json`; do not delete text to get under the check. English-only i18n is the
current ruling, so this counts literals not yet moved to `t()`.

The SLM console's template text and attributes are held at zero by
`slm_frontend_bare_ui_literals_test.py` on the same detector and the same rule;
this ratchet adds the script kind there and pins the main GUI per file.

**Known false positives, counted on purpose** rather than special-cased: proper
nouns and product or language names (`Python`, `Vue`) and units (`KB/s`) are
not translatable prose in the strict sense but are not mechanically separable
from a one-word label. They stay in the counts; a migration that moves them to
`t()` is harmless and one that leaves them is recorded here.
"""

from __future__ import annotations

import json
from pathlib import Path

#: The JSON beside this module: ``repo-relative .vue path -> [[kind, literal], ...]``.
BASELINE_PATH = Path(__file__).with_name("hardcoded_ui_literal_baseline_12732.json")

#: ``path -> (kind, literal)`` pairs not yet moved to ``t()``; a multiset, order-free.
HARDCODED_UI_LITERAL_IDS: dict[str, list[tuple[str, str]]] = {
    f: [(k, t) for k, t in v] for f, v in json.loads(BASELINE_PATH.read_text(encoding="utf-8")).items()
}

#: ``path -> how many``, derived from the identities (kept for the reach and sort checks).
HARDCODED_UI_LITERALS: dict[str, int] = {f: len(v) for f, v in HARDCODED_UI_LITERAL_IDS.items()}
