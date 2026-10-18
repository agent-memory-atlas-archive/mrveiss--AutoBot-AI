# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Hard-coded English UI text in `.vue` files, found three ways (#12732).

Enumeration is a filesystem walk under the repo root; the three matchers are
regexes. `hardcoded_ui_literal_ratchet_12732_test.py` re-derives the template
and attribute kinds with a different enumeration (`git ls-files`) and a
different parser (`html.parser`), so a mistake here is not repeated there.

The boundary and the shrink rule are stated in
`repo_tests/_hardcoded_ui_literal_baseline.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

from repo_tests._reach import declare

APP_TREES: tuple[Path, ...] = (
    Path("autobot-frontend/src"),
    Path("autobot-slm-frontend/src"),
)
_EXCLUDED_NAME_PARTS = (".stories.", ".spec.", ".test.")
KINDS = ("template-text", "attribute", "script")

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TEMPLATE = re.compile(r"<template\b.*</template>", re.DOTALL)
_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL)
_STYLE = re.compile(r"<style\b[^>]*>.*?</style>", re.DOTALL)
_TEXT = re.compile(r"""> *\s*([A-Za-z][A-Za-z ,.!?'’()-]{2,}?)\s*<""")
_TWO_LOWER = re.compile(r"[a-z]{2}")
_ATTRIBUTE = re.compile(r"""(?<![:@\w-])(?:title|placeholder|aria-label|label|alt)="([A-Za-z][^"{}]{2,})\"""")
_SCRIPT_LITERAL = re.compile(
    r"""(?:\.value\s*=|message:|title:|confirm\(|alert\(|Error\()\s*[`'"]([A-Z][a-z][^`'"]{3,})[`'"]"""
)

Literal = tuple[str, str]


def is_scanned_path(rel: str) -> bool:
    """The boundary predicate, shared by name only: the re-derivation restates it."""
    parts = rel.split("/")
    name = parts[-1]
    return not ("__tests__" in parts or any(p in name for p in _EXCLUDED_NAME_PARTS))


def scanned_files(root: Path) -> list[str]:
    """Repo-relative POSIX paths of every `.vue` file in scope, sorted."""
    out: list[str] = []
    for tree in APP_TREES:
        base = root / tree
        if not base.is_dir():
            continue
        for path in base.rglob("*.vue"):
            rel = path.relative_to(root).as_posix()
            if "node_modules" not in path.parts and is_scanned_path(rel):
                out.append(rel)
    return sorted(out)


def _template_region(source: str) -> str:
    """The `<template>` section: script and style blocks, then comments, removed first.

    A JSDoc example in `<script>` that mentions `<template #x>` otherwise starts
    the greedy match inside the script and drags the whole block in -- found by
    the parser re-derivation (`DataTable.vue` reported a literal from a comment).
    """
    match = _TEMPLATE.search(_COMMENT.sub("", _STYLE.sub("", _SCRIPT.sub("", source))))
    return match.group(0) if match else ""


def _template_text(source: str) -> list[Literal]:
    region = _template_region(source)
    found = []
    for raw in _TEXT.findall(region):
        text = raw.strip()
        if len(text) >= 3 and _TWO_LOWER.search(text):
            found.append(("template-text", text))
    return found


def _attributes(source: str) -> list[Literal]:
    return [("attribute", text) for text in _ATTRIBUTE.findall(_template_region(source))]


def _scripts(source: str) -> list[Literal]:
    found: list[Literal] = []
    for block in _SCRIPT.findall(source):
        found += [("script", text) for text in _SCRIPT_LITERAL.findall(block)]
    return found


def literals_in(source: str) -> list[Literal]:
    """Every (kind, literal) in one `.vue` source, in kind order."""
    return _template_text(source) + _attributes(source) + _scripts(source)


def scan(root: Path) -> dict[str, list[Literal]]:
    """Per-file literals for every file that has at least one."""
    out: dict[str, list[Literal]] = {}
    for rel in scanned_files(root):
        found = literals_in((root / rel).read_text(encoding="utf-8", errors="replace"))
        if found:
            out[rel] = found
    return out


REACH_UI_LITERAL_FILES = declare(
    "hardcoded-ui-literal-scan",
    discover=scanned_files,
    floor=550,
    growth=40,
    skips=0,
    what=".vue files scanned for hard-coded UI text",
    roots=tuple(f"{tree.as_posix()}/" for tree in APP_TREES),
)
