# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""Every research document lists the issues it produced (#18967).

A research document and the issues filed from it were linked one way at best: 25 of 56
documents had no issue pointing back at them, and 11 were pointed at by issues the
document itself never listed. A reader landing on either side could not reach the other.

This guard holds the in-repo half: each ``docs/research/*.md`` (except ``_index.md``)
carries a ``## Linked issues`` section with at least one ``#N`` reference, or the
explicit ``None filed yet``. An absent section and an empty one are both failures,
because "nothing was filed" must be stated, never implied by omission.

**Stated limit.** The other direction -- each listed issue mentioning the document --
lives on GitHub, not in the tree, so no repo test can assert it. The ``research`` skill
carries that half as a rule.
"""

from __future__ import annotations

import re
from pathlib import Path

from repo_tests._paths import repo_root

#: The section heading, at level 2, anywhere in the document.
_SECTION = re.compile(r"^## Linked issues[ \t]*$", re.MULTILINE)

#: The next level-1 or level-2 heading ends the section.
_NEXT_HEADING = re.compile(r"^#{1,2} ", re.MULTILINE)

#: What makes a section non-empty: an issue reference, or the explicit none-marker.
_ISSUE_REF = re.compile(r"#\d{2,6}\b")
_NONE_FILED = "None filed yet"

#: Reach floor. A glob that matched nothing would otherwise report every document clean.
#: 50 against 56 today; it bounds a moved directory, not the population's growth.
_MIN_DOCS = 50


def _research_docs() -> list[Path]:
    docs_dir = repo_root() / "docs" / "research"
    return sorted(p for p in docs_dir.glob("*.md") if p.name != "_index.md")


def section_body(text: str) -> str | None:
    """The text of the ``## Linked issues`` section, or None when there is none."""
    match = _SECTION.search(text)
    if match is None:
        return None
    rest = text[match.end() :]
    end = _NEXT_HEADING.search(rest)
    return rest[: end.start()] if end else rest


def unlinked(docs: dict[str, str]) -> list[str]:
    """Names of documents whose section is missing or lists nothing."""
    offenders: list[str] = []
    for name, text in docs.items():
        body = section_body(text)
        if body is None or not (_ISSUE_REF.search(body) or _NONE_FILED in body):
            offenders.append(name)
    return offenders


def test_the_research_directory_holds_a_population_worth_asserting_on() -> None:
    """Reach. A moved directory would otherwise pass as 'every document linked'."""
    count = len(_research_docs())
    assert count >= _MIN_DOCS, (
        f"only {count} research documents found, below the floor of {_MIN_DOCS} -- the "
        "directory has moved, so a clean result below would mean nothing was read"
    )


def test_every_research_doc_lists_its_issues() -> None:
    """#18967: the doc names the issues it produced, or says none were filed."""
    docs = {p.name: p.read_text(encoding="utf-8") for p in _research_docs()}
    offenders = unlinked(docs)
    assert not offenders, (
        f"these research documents have no '## Linked issues' section listing an issue "
        f"or saying '{_NONE_FILED}': {offenders}. A reader of the document cannot reach "
        "the work it produced -- list the issues, and add the document's path to each issue."
    )


def test_a_doc_without_the_section_is_reported() -> None:
    """The detector fires on absence."""
    assert unlinked({"a.md": "# T\n\nFindings, see #123.\n"}) == ["a.md"]


def test_an_empty_section_is_reported() -> None:
    """The detector fires on a section that lists nothing, even with refs elsewhere."""
    text = "# T\n\n## Linked issues\n\n## Appendix\n\nSee #123.\n"
    assert unlinked({"a.md": text}) == ["a.md"]


def test_a_listed_or_explicitly_empty_section_is_not_reported() -> None:
    """The contrast: an issue reference, or the explicit marker, both satisfy it."""
    listed = "# T\n\n## Linked issues\n\n- #18967 — the tracking issue\n"
    none = "# T\n\n## Linked issues\n\nNone filed yet.\n"
    assert unlinked({"a.md": listed, "b.md": none}) == []
