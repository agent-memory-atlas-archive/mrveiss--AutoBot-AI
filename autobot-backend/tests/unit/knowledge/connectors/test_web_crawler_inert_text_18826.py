# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""The crawl ingest path stores no JSON-LD or inline CSS in fact content (#18826)."""

from knowledge.connectors.web_crawler import _fetch_result_to_content
from web_fetch.extractors import extract_markdown
from web_fetch.types import FetchResult, RenderMode

_LONG_BODY = "A paragraph long enough that extraction takes the markdownify path, not the short-page fallback. " * 2
_HTML = (
    '<html><head><script type=\'application/ld+json\'>{"@type": "WebPage", "name": "LD-MARK"}</script>'
    "<style>.CSS-MARK{}</style></head><body><main><p>Body text for the knowledge base. "
    + _LONG_BODY
    + "</p></main></body></html>"
)


def _fetched(markdown: str) -> FetchResult:
    return FetchResult(
        url="https://example.com/docs",
        success=True,
        markdown=markdown,
        title="docs",
        render_mode=RenderMode.AUTO,
        source="bs4",
    )


def test_crawled_page_content_carries_no_json_ld_or_css():
    _title, markdown = extract_markdown(_HTML)
    content = _fetch_result_to_content(_fetched(markdown), "c")
    assert content is not None and "Body text for the knowledge base." in content.content
    assert "LD-MARK" not in content.content and "CSS-MARK" not in content.content
