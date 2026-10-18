# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
r"""No new hard-coded English UI text in a `.vue` file (#12732).

The boundary, the kinds of literal and the shrink rule are in
`repo_tests/_hardcoded_ui_literal_baseline.py`; the detector is
`repo_tests/_hardcoded_ui_literal_detect.py`.

**Second derivation (template-text and attribute).** The detector walks the
filesystem and matches with regexes. The re-derivation below enumerates with
`git ls-files` and parses the template with `html.parser`, applying the same
predicate (length, lowercase pair, unbound attribute names, comments dropped),
and the two are compared as SETS of (file, kind, literal).

One enumerated normalisation: the parser decodes character references in
attribute values (`&#10;` -> newline), the regex reads the source text, so the
regex side is `html.unescape`d before comparing. Two real differences were found
and resolved before freezing: the first `git ls-files` pathspec missed the
top-level `App.vue` of each app (re-derivation bug), and a JSDoc example in a
`<script>` mentioning `<template #x>` made the detector's greedy template match
start inside the script, reporting `Edit` from `DataTable.vue` (detector bug,
fixed by removing script and style blocks first).

**The script kind stays un-re-derived, stated rather than left ambiguous.**
It matches JavaScript string literals after a handful of call shapes; the only
independent method would be a JavaScript parser, which this suite does not
carry, and a hand-rolled one would share the regex's blind spots. It is covered
by its known positive and by synthetic fixtures instead.
"""

from __future__ import annotations

import html
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from repo_tests._hardcoded_ui_literal_baseline import HARDCODED_UI_LITERALS
from repo_tests._hardcoded_ui_literal_detect import (
    APP_TREES,
    REACH_UI_LITERAL_FILES,
    literals_in,
    scan,
    scanned_files,
)
from repo_tests._paths import repo_root

from autobot_shared.paths import scrubbed_git_env

_PANEL = "autobot-frontend/src/components/knowledge/connectors/ConnectorHistoryPanel.vue"
_PANEL_LITERALS = {
    ("template-text", "Sync History"),
    ("template-text", "Running"),
    ("template-text", "No sync history yet."),
    ("attribute", "Close"),
}
_FIX = (
    "Use t('...') with an English key in en.json (English-only i18n; SLM locales go "
    "through scripts/lift_locale_translations.py). The untranslated ratchet may rise by "
    "exactly the moved keys (owner ruling, #12732)."
)


@pytest.fixture(scope="module")
def measured() -> dict[str, list[tuple[str, str]]]:
    return scan(repo_root())


class TestThePopulationOnlyShrinks:
    def test_no_file_gains_a_literal(self, measured):
        grew = {
            f: (HARDCODED_UI_LITERALS.get(f, 0), len(v))
            for f, v in measured.items()
            if len(v) > HARDCODED_UI_LITERALS.get(f, 0)
        }
        assert not grew, f"hard-coded UI text grew (file: recorded, now) {grew}. {_FIX}"

    def test_a_shrink_is_recorded_in_the_commit_that_did_the_work(self, measured):
        shrunk = {
            f: (n, len(measured.get(f, [])))
            for f, n in HARDCODED_UI_LITERALS.items()
            if 0 < len(measured.get(f, [])) < n
        }
        assert not shrunk, f"lower these in _hardcoded_ui_literal_baseline.py (recorded, now): {shrunk}"

    def test_a_file_that_reaches_zero_leaves_the_dict(self, measured):
        gone = sorted(f for f in HARDCODED_UI_LITERALS if f not in measured)
        assert not gone, f"now at 0 literals (or deleted); remove from the baseline: {gone}"

    def test_the_baseline_has_no_zero_or_foreign_entries(self):
        files = set(scanned_files(repo_root()))
        bad = [f for f, n in HARDCODED_UI_LITERALS.items() if n <= 0 or f not in files]
        assert not bad, f"entries that are zero or outside the boundary: {bad}"

    def test_the_baseline_is_sorted(self):
        keys = list(HARDCODED_UI_LITERALS)
        assert keys == sorted(keys)


class TestTheBoundaryDoesNotMoveSilently:
    def test_the_sweep_reaches_the_tree(self):
        read = len(scanned_files(repo_root()))
        assert read >= REACH_UI_LITERAL_FILES.floor, f"opened only {read} files, floor {REACH_UI_LITERAL_FILES.floor}"

    def test_both_apps_are_enumerated(self):
        files = scanned_files(repo_root())
        for tree in APP_TREES:
            assert any(f.startswith(tree.as_posix() + "/") for f in files), tree

    def test_a_known_component_is_in_and_non_product_files_are_out(self):
        files = scanned_files(repo_root())
        assert _PANEL in files
        tracked = _git_vue_files()
        assert not [f for f in files if "__tests__/" in f or ".stories." in f or ".spec." in f]
        excluded = [f for f in tracked if f not in files]
        assert all("__tests__/" in f or ".stories." in f or ".spec." in f or ".test." in f for f in excluded)

    def test_each_app_has_pinned_literals(self):
        for tree in APP_TREES:
            assert any(f.startswith(tree.as_posix() + "/") for f in HARDCODED_UI_LITERALS), tree


class TestKnownPositive:
    def test_the_detector_finds_the_history_panel_literals(self):
        text = (repo_root() / _PANEL).read_text(encoding="utf-8")
        assert _PANEL_LITERALS <= set(literals_in(text))


class TestEachMatcherOnSyntheticSources:
    def _kinds(self, source: str, kind: str) -> list[str]:
        return [t for k, t in literals_in(source) if k == kind]

    def test_template_text_is_found(self):
        assert self._kinds("<template><p>Hello world</p></template>", "template-text") == ["Hello world"]

    def test_template_text_negatives(self):
        src = "<template><p>{{ t('x') }}</p><i>OK</i><b>ab</b><u>HELP</u>" "<!-- <s>Hidden words</s> --></template>"
        assert self._kinds(src, "template-text") == []

    def test_a_template_tag_in_a_script_comment_does_not_open_the_template(self):
        src = "<script>\n/* <template #x> <b>Edit</b> */\n</script>\n<template><p>Real text</p></template>"
        assert self._kinds(src, "template-text") == ["Real text"]

    def test_text_outside_the_template_is_ignored(self):
        assert self._kinds("<script>const a = '<p>Hello there</p>'</script>", "template-text") == []

    def test_unbound_attribute_is_found(self):
        src = '<template><input placeholder="Search here" aria-label="Go"></template>'
        assert self._kinds(src, "attribute") == ["Search here"]

    def test_attribute_negatives(self):
        src = (
            '<template><a :title="x" @title="y" data-title="Some words" '
            'title="{{ a }}" label="x"></a><!-- <i alt="Hidden words"> --></template>'
        )
        assert self._kinds(src, "attribute") == []

    def test_script_literal_is_found(self):
        src = "<script>msg.value = 'Failed to load'; throw new Error(\"Bad request\")</script>"
        assert self._kinds(src, "script") == ["Failed to load", "Bad request"]

    def test_script_negatives(self):
        src = "<script>a.value = 'lower case'; b.value = t('x'); title: 'A'</script>"
        assert self._kinds(src, "script") == []

    def test_script_text_in_template_is_not_a_script_literal(self):
        assert self._kinds("<template><p>message: 'Hello there'</p></template>", "script") == []


# ---- second derivation -------------------------------------------------------

_ATTR_NAMES = {"title", "placeholder", "aria-label", "label", "alt"}
_TEXT_CLASS = re.compile(r"[A-Za-z][A-Za-z ,.!?'’()-]{2,}")
_ATTR_VALUE = re.compile(r"[A-Za-z][^\"{}]{2,}")
_LOWER_PAIR = re.compile(r"[a-z][a-z]")


def _git_vue_files() -> list[str]:
    """Tracked `.vue` paths via git, a different enumeration from the filesystem walk."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *(t.as_posix() for t in APP_TREES)],
        cwd=repo_root(),
        env=scrubbed_git_env(),
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return sorted(p for p in out.split("\0") if p.endswith(".vue"))


def _in_boundary(rel: str) -> bool:
    """Restated, not imported: `__tests__/` directory or a stories/spec/test name."""
    name = rel.rsplit("/", 1)[-1]
    if "/__tests__/" in rel:
        return False
    return not any(marker in name for marker in (".stories.", ".spec.", ".test."))


class _Template(HTMLParser):
    """Collects the same two kinds from the parsed tree of one `.vue` file."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.found: set[tuple[str, str]] = set()

    def handle_starttag(self, tag, attrs):
        if tag == "template":
            self.depth += 1
        elif self.depth:
            self._attrs(attrs)

    def handle_startendtag(self, tag, attrs):
        if self.depth and tag != "template":
            self._attrs(attrs)

    def handle_endtag(self, tag):
        if tag == "template" and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        text = data.strip()
        if self.depth and len(text) >= 3 and _TEXT_CLASS.fullmatch(text) and _LOWER_PAIR.search(text):
            self.found.add(("template-text", text))

    def _attrs(self, attrs):
        for name, value in attrs:
            if name in _ATTR_NAMES and value and _ATTR_VALUE.fullmatch(value):
                self.found.add(("attribute", value))


def _parsed_literals(source: str) -> set[tuple[str, str]]:
    parser = _Template()
    parser.feed(source)
    parser.close()
    return parser.found


def _rederived(root: Path) -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for rel in _git_vue_files():
        if _in_boundary(rel):
            source = (root / rel).read_text(encoding="utf-8", errors="replace")
            out |= {(rel, kind, text) for kind, text in _parsed_literals(source)}
    return out


def _detected(root: Path) -> set[tuple[str, str, str]]:
    return {
        (rel, kind, html.unescape(text))
        for rel, found in scan(root).items()
        for kind, text in found
        if kind != "script"
    }


class TestSecondDerivation:
    def test_the_rederivation_finds_its_own_known_positive(self):
        found = _parsed_literals((repo_root() / _PANEL).read_text(encoding="utf-8"))
        assert _PANEL_LITERALS <= found

    def test_the_parser_applies_the_predicate_on_synthetic_input(self):
        src = (
            '<template><p>Hello world</p><i>OK</i><b>{{ t("x") }}</b>'
            '<!-- <s>Hidden words</s> --><a :title="x" title="Real words"></a></template>'
        )
        assert _parsed_literals(src) == {("template-text", "Hello world"), ("attribute", "Real words")}

    def test_enumerations_agree(self):
        by_git = {f for f in _git_vue_files() if _in_boundary(f)}
        assert by_git == set(scanned_files(repo_root()))

    def test_literal_sets_agree(self):
        root = repo_root()
        detected, rederived = _detected(root), _rederived(root)
        assert detected == rederived, (
            f"only regex: {sorted(detected - rederived)[:10]}; " f"only parser: {sorted(rederived - detected)[:10]}"
        )
