# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Content-Type dispatch for fetched bodies: only HTML reaches the HTML extractor (#18826).

``WebFetcher`` ran every non-jina body through the HTML extractor whatever the server
said it was, so a plain-text file was HTML-parsed and a PDF or image was decoded as
UTF-8 noise and stored as page text. Before #7401 the dispatch lived in
``SystemIntegration._process_web_content``; #7401 moved fetching into ``WebFetcher``
and left that dispatch behind with no caller. This is its home now.

=====================================  =====================================================
Content-Type                           Result
=====================================  =====================================================
``text/html``, ``application/xhtml+xml``  the one HTML extractor (:func:`extract_page`)
``text/plain``, ``text/markdown``      the text, whitespace normalised
``application/json``, ``*/*+json``     the JSON pretty-printed (passed through as text when
                                       it does not parse), as ``_process_web_content`` did
missing                                sniffed: HTML only when the body looks like HTML,
                                       ``unsupported`` when it looks binary, else text
anything else (binary, image, PDF)     :class:`UnsupportedContentType`, never extracted
=====================================  =====================================================
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Optional, Tuple, Union

from web_fetch.profile import IngestProfile, extract_page

#: How much of a body without a Content-Type is inspected to sniff its kind.
_SNIFF_CHARS = 2048
#: Share of U+FFFD replacement characters above which an undeclared body is binary.
_BINARY_REPLACEMENT_RATIO = 0.05
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_TEXT_MEDIA_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})
_HTML_SNIFF = re.compile(r"^\s*(<!--.*?-->\s*)*<(!doctype\s+html|html|head|body)[\s>]", re.IGNORECASE | re.DOTALL)


class BodyKind(str, Enum):
    """What a fetched body is, for extraction purposes."""

    HTML = "html"
    TEXT = "text"
    JSON = "json"
    UNSUPPORTED = "unsupported"


class UnsupportedContentType(ValueError):
    """A fetched body that is not HTML, text or JSON; it is never extracted as a page."""

    def __init__(self, content_type: Optional[str]) -> None:
        self.content_type = content_type or ""
        super().__init__(f"unsupported content type {self.content_type or '(undeclared, binary)'!r}")


def media_type(content_type: Optional[str]) -> str:
    """Return the lowercased media type of a Content-Type header, parameters dropped."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def _sniff(body: str) -> BodyKind:
    """Classify a body that came without a Content-Type, conservatively."""
    head = body[:_SNIFF_CHARS]
    if "\x00" in head or (head and head.count("�") / len(head) > _BINARY_REPLACEMENT_RATIO):
        return BodyKind.UNSUPPORTED
    return BodyKind.HTML if _HTML_SNIFF.match(head) else BodyKind.TEXT


def classify_body(content_type: Optional[str], body: str) -> BodyKind:
    """Return the :class:`BodyKind` of *body* as declared by *content_type*."""
    declared = media_type(content_type)
    if not declared:
        return _sniff(body)
    if declared in _HTML_MEDIA_TYPES:
        return BodyKind.HTML
    if declared in _TEXT_MEDIA_TYPES:
        return BodyKind.TEXT
    if declared == "application/json" or declared.endswith("+json"):
        return BodyKind.JSON
    return BodyKind.UNSUPPORTED


def normalise_text(text: str) -> str:
    """Normalise line endings and whitespace while keeping paragraph breaks."""
    lines = (line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _json_text(body: str) -> str:
    """Pretty-print a JSON body; one that does not parse passes through as text."""
    try:
        return json.dumps(json.loads(body), indent=2, ensure_ascii=False)
    except ValueError:
        return normalise_text(body)


def page_from_body(
    body: str, content_type: Optional[str], profile: Union[IngestProfile, str], url: str
) -> Tuple[BodyKind, str, str]:
    """Return ``(kind, title, text)`` for a fetched body; raise :class:`UnsupportedContentType` otherwise."""
    kind = classify_body(content_type, body)
    if kind is BodyKind.HTML:
        document = extract_page(body, profile, base_url=url)
        return kind, document.info.get("title", ""), document.text
    if kind is BodyKind.TEXT:
        return kind, "", normalise_text(body)
    if kind is BodyKind.JSON:
        return kind, "", _json_text(body)
    raise UnsupportedContentType(content_type)
