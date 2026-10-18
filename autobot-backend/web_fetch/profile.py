# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The extraction-profile seam: one HTML extractor, selected by task (#18839, #18826).

What is kept from a web page depends on the task. Every HTML ingest entry point
(``POST /knowledge/crawl``, the web crawler connector, ``WebFetcher``'s non-jina
path, ``POST /knowledge/scrape`` and the chat ``extract_content`` tool) takes an
explicit :class:`IngestProfile` and passes it down to :func:`extract_page`, the
one function that turns a page into the canonical ``ExtractedDocument``.

``document`` (text and structure only) is implemented and is the default.
``media`` (#18840) and ``page_copy`` (#18841) are named so a caller can ask for
them, and refuse with :class:`ProfileNotImplementedError` until they land. An
unknown profile is rejected; it is never treated as ``document``.
"""

from __future__ import annotations

from enum import Enum
from typing import Union

from media.document.extraction import ExtractedDocument
from media.document.html_extraction import extract_html


class IngestProfile(str, Enum):
    """What an HTML ingest keeps from a page."""

    DOCUMENT = "document"
    MEDIA = "media"
    PAGE_COPY = "page_copy"


#: The issue that implements each profile not yet available.
_PENDING = {IngestProfile.MEDIA: "#18840", IngestProfile.PAGE_COPY: "#18841"}


class ProfileNotImplementedError(NotImplementedError):
    """A known profile whose extractor has not landed yet."""


def resolve_profile(profile: Union[IngestProfile, str]) -> IngestProfile:
    """Return *profile* as an :class:`IngestProfile`; an unknown value raises ``ValueError``."""
    if isinstance(profile, IngestProfile):
        return profile
    try:
        return IngestProfile(profile)
    except ValueError:
        known = ", ".join(member.value for member in IngestProfile)
        raise ValueError(f"unknown ingest profile {profile!r}; expected one of: {known}") from None


def require_implemented(profile: Union[IngestProfile, str]) -> IngestProfile:
    """Resolve *profile* and refuse one whose extractor is not implemented yet."""
    resolved = resolve_profile(profile)
    if resolved in _PENDING:
        raise ProfileNotImplementedError(
            f"ingest profile {resolved.value!r} is not implemented yet ({_PENDING[resolved]}); use 'document'"
        )
    return resolved


def extract_page(html: str, profile: Union[IngestProfile, str], base_url: str = "") -> ExtractedDocument:
    """Extract *html* under *profile* into the canonical ``ExtractedDocument``.

    The one HTML extractor for every ingest entry point. *base_url* resolves
    relative link targets.
    """
    require_implemented(profile)
    return extract_html(html, base_url=base_url)
