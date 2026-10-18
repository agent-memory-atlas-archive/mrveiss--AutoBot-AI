# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Heading/section structure of a canonical :class:`ExtractedDocument` (#18826).

``ExtractedDocument`` carried text, pages and tables but no heading hierarchy, so
an HTML page's structure had nowhere to live except as markup baked into the text.
Owner decision on #18826, option (a): the structure is part of the canonical model,
shared by every producer -- HTML, DOCX and PDF -- and the text is rendered from it.
Structural markdown is **not** the canonical text format. HTML
(:mod:`media.document.html_extraction`) populates it today; DOCX headings belong to
#17985, which must read outline levels rather than localised style names.

A document is an ordered tuple of :class:`Section`. Each has a heading level (1-6
for HTML; ``0`` for content before the first heading), the heading text, and its
:class:`Block` content: paragraphs, lists, tables, code and quotes. Link targets
travel as :class:`Link` values on the block that carries them.

This module imports nothing from ``extraction.py`` at runtime, so
``extraction.py`` can name :class:`Section` in its dataclass without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Sequence, Tuple


class BlockKind(str, Enum):
    """The block types a section may hold."""

    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    CODE = "code"
    QUOTE = "quote"


@dataclass(frozen=True)
class Link:
    """A hyperlink: its visible text and its absolute target."""

    text: str
    target: str


@dataclass(frozen=True)
class ListItem:
    """One list item; ``depth`` is 0 for a top-level item, 1 for a nested one, ..."""

    text: str
    depth: int = 0
    ordered: bool = False


@dataclass(frozen=True)
class Block:
    """One unit of section content.

    ``text`` holds a paragraph, quote or code body; ``items`` a list; ``rows`` a
    table as rows of cell strings, the same shape ``ExtractedDocument.tables`` uses.
    """

    kind: BlockKind
    text: str = ""
    items: Tuple[ListItem, ...] = ()
    rows: Tuple[Tuple[str, ...], ...] = ()
    links: Tuple[Link, ...] = ()


@dataclass(frozen=True)
class Section:
    """A heading and the blocks under it, up to the next heading."""

    level: int
    heading: str
    blocks: Tuple[Block, ...] = ()


def _render_list(items: Sequence[ListItem]) -> str:
    """Render list items one per line, indented by depth, numbered when ordered."""
    counters: Dict[int, int] = {}
    lines: List[str] = []
    for item in items:
        counters = {depth: count for depth, count in counters.items() if depth <= item.depth}
        counters[item.depth] = counters.get(item.depth, 0) + 1
        marker = f"{counters[item.depth]}." if item.ordered else "-"
        lines.append(f"{'  ' * item.depth}{marker} {item.text}")
    return "\n".join(lines)


def render_block(block: Block) -> str:
    """Render one block as plain text; tables go through the shared table renderer."""
    if block.kind is BlockKind.LIST:
        return _render_list(block.items)
    if block.kind is BlockKind.TABLE:
        # Local import: provenance imports extraction, which names Section.
        from media.document.provenance import render_tables

        return render_tables([[list(row) for row in block.rows]])
    return block.text


def render_sections(sections: Iterable[Section]) -> str:
    """Render sections as plain text: each heading on its own line, then its blocks."""
    parts: List[str] = []
    for section in sections:
        if section.heading:
            parts.append(section.heading)
        parts.extend(text for text in (render_block(block) for block in section.blocks) if text)
    return "\n\n".join(parts)
