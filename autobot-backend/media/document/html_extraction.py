# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""HTML into the canonical :class:`ExtractedDocument`: document text and structure only (#18826).

HTML is the third producer of the canonical model, beside PDF and DOCX in
:mod:`media.document.extraction`. It is an **allowlist**: a crawled page is stored
in AutoBot's own format, and nothing of the source site's presentation survives.

Kept, as :mod:`media.document.sections` structure: ``h1``-``h6`` headings,
``p`` paragraphs, ``ul``/``ol``/``li`` lists with nesting, ``table`` rows and
``th``/``td`` cells, ``pre`` code, ``blockquote`` quotes, ``a`` links (the
absolute target only), and ``br``/``hr`` breaks. ``strong``/``em``/``code`` and
every other inline element contribute their text and nothing else.

Dropped **with** their text: :data:`DROPPED_TAGS` (scripts, styles, JSON-LD,
embeds, form controls) and site chrome -- ``nav``, ``aside``, ``dialog``, a
page-level ``header``/``footer``, any element whose ``role`` is in
:data:`CHROME_ROLES`, and hidden elements. Generic containers (``div``, ``span``,
``section``, ``article``, ``main``, ...) are unwrapped to their content. No
attribute survives except a link's target. ``<main>``, else a single
``<article>``, is the content root when the page has one.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import NavigableString, PreformattedString, Tag

from autobot_shared.logging_manager import get_logger
from media.document.extraction import ExtractedDocument
from media.document.sections import Block, BlockKind, Link, ListItem, Section, render_sections

logger = get_logger(__name__)

#: Tags whose inner text is never page content: code, CSS, JSON-LD, fallbacks.
INERT_TAGS = ("script", "style", "noscript", "template")

#: Page chrome. ``header``/``footer`` count only at page level (see :func:`_is_chrome`).
CHROME_TAGS = ("nav", "header", "footer", "aside")

#: Every tag removed together with its text before the walk.
DROPPED_TAGS = INERT_TAGS + (
    "head",
    "svg",
    "math",
    "iframe",
    "object",
    "embed",
    "canvas",
    "img",
    "picture",
    "video",
    "audio",
    "map",
    "input",
    "button",
    "select",
    "textarea",
    "option",
    "optgroup",
    "datalist",
    "output",
    "progress",
    "meter",
    "label",
)

#: ARIA landmark roles that mark site chrome rather than document content.
CHROME_ROLES = frozenset({"navigation", "banner", "contentinfo", "complementary", "dialog", "alertdialog"})

_ALWAYS_CHROME = frozenset({"nav", "aside", "dialog"})
# A header/footer inside one of these belongs to that content, not to the site (HTML-AAM).
_SCOPING_TAGS = ("article", "main", "section", "aside", "nav")
_HEADINGS = {f"h{level}": level for level in range(1, 7)}
_LIST_TAGS = ("ul", "ol")
_LINK_SCHEMES = frozenset({"http", "https", "mailto"})
# Elements that break a line when their text is read inline (inside a quote or a cell).
_LINE_BREAKING = frozenset({"p", "div", "li", "tr", "pre", "blockquote", "dt", "dd"}) | frozenset(_HEADINGS)
_CONTAINERS = frozenset(
    {"html", "body", "main", "article", "section", "div", "form", "fieldset", "figure", "figcaption", "details"}
    | {"summary", "dl", "dt", "dd", "address", "center", "header", "footer", "hgroup", "search", "legend"}
)


def extract_title(soup: Any) -> str:
    """Extract the page title from ``og:title`` or ``<title>``, at most 300 characters."""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()[:300]
    tag = soup.find("title")
    return tag.get_text(strip=True)[:300] if tag else ""


def _is_chrome(tag: Any) -> bool:
    """Whether *tag* is site chrome or hidden, and so dropped with its text."""
    if (tag.get("role") or "").strip().lower() in CHROME_ROLES:
        return True
    if tag.get("hidden") is not None or (tag.get("aria-hidden") or "").strip().lower() == "true":
        return True
    if tag.name in _ALWAYS_CHROME:
        return True
    return tag.name in ("header", "footer") and tag.find_parent(_SCOPING_TAGS) is None


def drop_non_content(soup: Any) -> None:
    """Remove, in place, every element that is not document content, text included."""
    for tag in soup(list(DROPPED_TAGS)):
        tag.decompose()
    for tag in soup.find_all(_is_chrome):
        if not tag.decomposed:
            tag.decompose()


def _content_root(soup: Any) -> Any:
    """Prefer ``<main>``, then a sole ``<article>``, then ``<body>``, then the whole tree."""
    main = soup.find("main") or soup.find(attrs={"role": "main"})
    if main is not None:
        return main
    articles = soup.find_all("article")
    if len(articles) == 1:
        return articles[0]
    return soup.body or soup


def _clean(raw: str) -> str:
    """Collapse whitespace runs, keeping the line breaks a ``<br>`` produced."""
    lines = (re.sub(r"[^\S\n]+", " ", line).strip() for line in raw.split("\n"))
    return "\n".join(line for line in lines if line)


def _one_line(raw: str) -> str:
    """Clean text that must stay on a single line: headings, list items, table cells."""
    return _clean(raw).replace("\n", " ")


def _link_target(href: Any, base_url: str) -> str:
    """Resolve *href* to an absolute http(s)/mailto target, or ``""`` to drop it."""
    href = (href or "").strip() if isinstance(href, str) else ""
    if not href or href.startswith("#"):
        return ""
    resolved = urljoin(base_url, href) if base_url else href
    return resolved if urlparse(resolved).scheme.lower() in _LINK_SCHEMES else ""


class _Inline:
    """Reads an element's text inline, recording the links it carries."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.links: List[Link] = []

    def text(self, node: Any, skip: Tuple[str, ...] = ()) -> str:
        """Return *node*'s visible text; tags named in *skip* contribute nothing."""
        if isinstance(node, NavigableString):
            return "" if isinstance(node, PreformattedString) else re.sub(r"\s+", " ", str(node))
        if not isinstance(node, Tag) or node.name in skip:
            return ""
        if node.name == "br":
            return "\n"
        inner = "".join(self.text(child, skip) for child in node.children)
        if node.name == "a":
            return self._link(node, inner)
        return f"\n{inner}\n" if node.name in _LINE_BREAKING else inner

    def _link(self, node: Any, inner: str) -> str:
        """Record the link's target and render it after the link text."""
        target = _link_target(node.get("href"), self.base_url)
        if not target:
            return inner
        label = _one_line(inner)
        self.links.append(Link(text=label, target=target))
        return inner if label == target else f"{inner} ({target})"


def _paragraph(node: Any, base_url: str) -> Optional[Block]:
    reader = _Inline(base_url)
    text = _clean(reader.text(node))
    return Block(kind=BlockKind.PARAGRAPH, text=text, links=tuple(reader.links)) if text else None


def _quote(node: Any, base_url: str) -> Optional[Block]:
    reader = _Inline(base_url)
    text = _clean(reader.text(node))
    return Block(kind=BlockKind.QUOTE, text=text, links=tuple(reader.links)) if text else None


def _code(node: Any, base_url: str) -> Optional[Block]:
    text = node.get_text().strip("\n")
    return Block(kind=BlockKind.CODE, text=text) if text.strip() else None


def _list_items(node: Any, depth: int, reader: _Inline) -> List[ListItem]:
    """Items of the list *node*, each followed by its nested lists one level deeper."""
    items: List[ListItem] = []
    for li in node.find_all("li"):
        if li.find_parent(_LIST_TAGS) is not node:
            continue
        text = _one_line(reader.text(li, skip=_LIST_TAGS))
        if text:
            items.append(ListItem(text=text, depth=depth, ordered=node.name == "ol"))
        for sub in li.find_all(_LIST_TAGS):
            if sub.find_parent(_LIST_TAGS) is node:
                items.extend(_list_items(sub, depth + 1, reader))
    return items


def _list(node: Any, base_url: str) -> Optional[Block]:
    reader = _Inline(base_url)
    items = tuple(_list_items(node, 0, reader))
    return Block(kind=BlockKind.LIST, items=items, links=tuple(reader.links)) if items else None


def _table(node: Any, base_url: str) -> Optional[Block]:
    reader = _Inline(base_url)
    rows = []
    for tr in node.find_all("tr"):
        if tr.find_parent("table") is not node:
            continue
        cells = tuple(_one_line(reader.text(cell)) for cell in tr.find_all(["th", "td"], recursive=False))
        if any(cells):
            rows.append(cells)
    return Block(kind=BlockKind.TABLE, rows=tuple(rows), links=tuple(reader.links)) if rows else None


_BLOCK_READERS: Dict[str, Callable[[Any, str], Optional[Block]]] = {
    "p": _paragraph,
    "blockquote": _quote,
    "pre": _code,
    "ul": _list,
    "ol": _list,
    "table": _table,
}


#: Tags that make an enclosing element a container to walk rather than inline text to read.
_STRUCTURAL = tuple(_HEADINGS) + tuple(_BLOCK_READERS) + ("hr",) + tuple(_CONTAINERS)


class _SectionBuilder:
    """Accumulates blocks under the current heading; loose inline text becomes a paragraph."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.sections: List[Section] = []
        self.level, self.heading = 0, ""
        self.blocks: List[Block] = []
        self.inline = _Inline(base_url)
        self.pending: List[str] = []

    def flush(self) -> None:
        """Turn pending loose inline text into a paragraph block."""
        text = _clean("".join(self.pending))
        if text:
            self.blocks.append(Block(kind=BlockKind.PARAGRAPH, text=text, links=tuple(self.inline.links)))
        self.pending, self.inline = [], _Inline(self.base_url)

    def add(self, block: Optional[Block]) -> None:
        self.flush()
        if block is not None:
            self.blocks.append(block)

    def open_section(self, level: int, heading: str) -> None:
        self.flush()
        self._close()
        self.level, self.heading = level, heading

    def _close(self) -> None:
        if self.heading or self.blocks:
            self.sections.append(Section(level=self.level, heading=self.heading, blocks=tuple(self.blocks)))
        self.blocks = []

    def finish(self) -> Tuple[Section, ...]:
        self.flush()
        self._close()
        return tuple(self.sections)


def _walk(node: Any, builder: _SectionBuilder) -> None:
    """Visit *node*'s children, routing each to a heading, a block or loose text."""
    for child in node.children:
        name = child.name if isinstance(child, Tag) else None
        if name in _HEADINGS:
            builder.open_section(_HEADINGS[name], _one_line(_Inline(builder.base_url).text(child)))
        elif name in _BLOCK_READERS:
            builder.add(_BLOCK_READERS[name](child, builder.base_url))
        elif name == "hr":
            builder.flush()
        elif name in _CONTAINERS or (name is not None and child.find(_STRUCTURAL) is not None):
            # A generic container -- or an inline element such as <span> wrapping blocks -- is unwrapped.
            builder.flush()
            _walk(child, builder)
            builder.flush()
        else:
            builder.pending.append(builder.inline.text(child))


def _sections_of(root: Any, base_url: str) -> Tuple[Section, ...]:
    """Build the section tree, degrading to one flat paragraph for pathologically deep markup."""
    builder = _SectionBuilder(base_url)
    try:
        _walk(root, builder)
    except RecursionError:
        logger.warning("HTML nesting too deep for structural extraction; keeping its text only (#18826)")
        text = _clean(root.get_text("\n"))
        return (Section(level=0, heading="", blocks=(Block(kind=BlockKind.PARAGRAPH, text=text),)),) if text else ()
    return builder.finish()


def extract_html(html: str, base_url: str = "") -> ExtractedDocument:
    """Extract an HTML page's document text and structure into ``ExtractedDocument`` (format ``html``).

    *base_url* resolves relative link targets; without it only absolute targets survive.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    title = extract_title(soup)
    drop_non_content(soup)
    sections = _sections_of(_content_root(soup), base_url)
    return ExtractedDocument(format="html", text=render_sections(sections), info={"title": title}, sections=sections)
