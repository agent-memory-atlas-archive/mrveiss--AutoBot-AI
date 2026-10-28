# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""No style rule targets an ``<i>`` icon that its own template no longer renders (#19491).

**The defect.** The user app replaced its ``<i class="fa…">`` icons with the kit-backed
``Icon`` component, which renders an ``<svg class="icon …">``, and left the CSS that
styled ``i`` behind. On 8731953af6 that was 207 rules in 89 components, each styling
nothing: icon colour, spacing and opacity silently gone.

**The boundary.** The count is every style rule, in a ``.vue`` file under
``autobot-frontend/src`` whose ``<template>`` contains no ``<i`` element, whose selector
names an ``i`` element (``.x i``, ``.x > i``, ``i.fa-…``). Comments and ``@``-rule headers
are skipped. A rule for an ``<i>`` that a child component renders would be a false
positive; none was found in the 207.

**The blind spot.** A file whose template still renders any ``<i>`` is skipped whole, so a
dead ``i`` rule beside a live one is not counted (121 ``i`` rules sit in such files on
7d26a6ed6c; at least 8 of those files also render ``Icon``, e.g. ``FileBrowser.vue``). Telling
those apart needs each rule matched against the template's structure, which this textual
detector does not do. It is a known under-count, not a clean result.

**The pin.** 200 bare ``i`` rules were retargeted to ``.icon``, Icon's root class (#19491).
The 7 left are ``i.fa-<name>`` rules that pick one icon among several in a container, so
each needs a class on its ``Icon`` rather than a blanket ``.icon``. The pin is exact and
down-only against the base (#19283).
"""

from __future__ import annotations

import re
from pathlib import Path

from repo_tests._paths import repo_root
from repo_tests._ratchet_base import base_table
from repo_tests._reach import declare

_FRONTEND_SRC = "autobot-frontend/src"
_TEMPLATE = re.compile(r"<template>(.*)</template>", re.S)
_HAS_I_ELEMENT = re.compile(r"<i[\s>/]")
# A commented-out `<i>` renders nothing, so it must not exempt the file.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_STYLE = re.compile(r"<style[^>]*>(.*?)</style>", re.S)
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_RULE = re.compile(r"([^{}]+)\{")
# An `i` element: after a combinator or at the start, then a class/id/attribute or the end
# of the compound. Functional pseudo-classes are stripped first, so `:not(i)` is not one.
_I_SELECTOR = re.compile(r"(?:^|[\s>+~,])i(?:[.#\[]|(?=\s*[{,:>\s]|$))")
_FUNCTIONAL_PSEUDO = re.compile(r":(?:not|is|where|has)\([^)]*\)")

# 207 measured on 8731953af6; 7 after the bare-`i` retarget (#19491). Lower it by what a PR fixes.
PINS = {"dead_icon_selector_rules": 7}

# Non-vacuity: a moved tree or a broken glob scans nothing and reports the same 0 as a clean one.
REACH_VUE_FILES = declare(
    "dead-icon-selector-vue-files",
    discover=lambda root: [p.relative_to(root).as_posix() for p in vue_files(root / _FRONTEND_SRC)],
    roots=(_FRONTEND_SRC,),
    floor=447,  # mid-window: population 467, window (427, 467)
    growth=40,
    skips=0,
    what=".vue files scanned for dead <i> selectors",
)


def vue_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.vue"))


def count_dead_icon_rules(root: Path) -> int:
    total = 0
    for path in vue_files(root):
        source = path.read_text(encoding="utf-8")
        template = _TEMPLATE.search(source)
        if template is None or _HAS_I_ELEMENT.search(_HTML_COMMENT.sub("", template.group(1))):
            continue
        for css in _STYLE.findall(source):
            for selector in _RULE.findall(_COMMENT.sub("", css)):
                if not selector.lstrip().startswith("@") and _I_SELECTOR.search(
                    " " + _FUNCTIONAL_PSEUDO.sub("", selector)
                ):
                    total += 1
    return total


class TestTheDetectorWorksBeforeItsOutputIsRead:
    def _w(self, root: Path, rel: str, body: str) -> None:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def test_it_counts_bare_and_classed_i_rules_when_the_template_has_no_i(self, tmp_path: Path) -> None:
        self._w(
            tmp_path,
            "A.vue",
            "<template><Icon name='x' /></template>\n<style>\n.a i { color: red; }\n"
            ".b > i { margin: 0; }\n.c i.fa-x { color: blue; }\n.d .icon { color: green; }\n</style>\n",
        )
        assert count_dead_icon_rules(tmp_path) == 3

    def test_attribute_and_id_forms_count_and_functional_pseudos_do_not(self, tmp_path: Path) -> None:
        self._w(
            tmp_path,
            "D.vue",
            "<template><Icon name='x' /></template>\n<style>\n.a i[class*=fa] { color: red; }\n"
            ".b i#x { color: red; }\n.c :not(i) { color: red; }\n.d :is(i, b) { color: red; }\n</style>\n",
        )
        assert count_dead_icon_rules(tmp_path) == 2

    def test_an_at_sign_inside_an_attribute_value_still_counts_but_an_at_rule_does_not(self, tmp_path: Path) -> None:
        self._w(
            tmp_path,
            "E.vue",
            "<template><Icon name='x' /></template>\n<style>\n[href=\"mailto:team@example.com\"] i { color: red; }\n"
            "@media (min-width: 1px) { .a .icon { color: red; } }\n@font-face { font-family: x; }\n</style>\n",
        )
        assert count_dead_icon_rules(tmp_path) == 1

    def test_a_template_that_still_renders_i_is_not_counted(self, tmp_path: Path) -> None:
        self._w(
            tmp_path, "B.vue", "<template><i class='fa fa-x'></i></template>\n<style>.a i { color: red; }</style>\n"
        )
        assert count_dead_icon_rules(tmp_path) == 0

    def test_a_commented_out_i_does_not_exempt_the_file(self, tmp_path: Path) -> None:
        self._w(
            tmp_path,
            "D.vue",
            "<template><!-- <i class='fa fa-x'></i> --><Icon name='x' /></template>\n<style>.a i { color: red; }</style>\n",
        )
        assert count_dead_icon_rules(tmp_path) == 1

    def test_words_and_classes_containing_i_are_not_an_i_element(self, tmp_path: Path) -> None:
        self._w(
            tmp_path,
            "C.vue",
            "<template><div /></template>\n<style>\n.item { a: b; }\n.list-i { a: b; }\n"
            "/* .a i { } */\n@media (min-width: 1px) { .x { a: b; } }\n</style>\n",
        )
        assert count_dead_icon_rules(tmp_path) == 0


def test_no_more_dead_icon_rules_than_pinned() -> None:
    root = repo_root() / _FRONTEND_SRC
    assert root.is_dir(), f"{_FRONTEND_SRC} has moved; this guard reads it by path"
    REACH_VUE_FILES.examined(repo_root())
    now = count_dead_icon_rules(root)
    pinned = PINS["dead_icon_selector_rules"]
    assert now <= pinned, (
        f"{now} style rules target an <i> their template does not render, pin allows {pinned} (#19491). "
        "Icon renders <svg class='icon'>: target `.icon` (or a class on that Icon), not `i`."
    )
    assert now == pinned, f"{now} dead icon rules, pin says {pinned}: lower PINS to {now} in this PR (#19491)."


def test_the_pin_was_not_raised_against_the_base() -> None:
    base = base_table(repo_root(), ("repo_tests/dead_icon_selector_ratchet_19491_test.py",), "PINS")
    if not base:
        return  # the guard is new at this base; nothing to compare against yet
    assert PINS["dead_icon_selector_rules"] <= base["dead_icon_selector_rules"], (
        f"PINS rose from {base['dead_icon_selector_rules']} to {PINS['dead_icon_selector_rules']} (#19491); "
        "the pin only goes down."
    )
