# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A crawled or scraped page's stored body is its document, never the site's presentation (#18826).

Driven through the real entry points -- the web crawler connector's per-page fetch and
``WebFetcher``'s non-jina path -- with only the network stubbed, so the assertion is on
the body that would be written to the knowledge base, whatever extractor sits behind it.
"""

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from knowledge.connectors.models import ConnectorConfig
from knowledge.connectors.web_crawler import WebCrawlerConnector, _fetch_result_to_content
from web_fetch.fetcher import WebFetcher
from web_fetch.types import RenderMode

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "kb_ingest_18826"
_PAGE_URL = "https://docs.example.org/reports/bugs-root-causes.html"


def _page_html() -> str:
    return (_FIXTURES / "page.html").read_text(encoding="utf-8")


def _assert_document_only(body: str) -> None:
    """No chrome, script, style or JSON-LD text, no markup; headings, table, list, code and link kept."""
    assert re.findall(r"[A-Z_-]*MARKER", body) == []
    for foreign in ("schema.org", "font-family", "text-align", "language-text", "<strong>", "**", "## "):
        assert foreign not in body
    for kept in (
        "0. Data collection and exact counts",
        "1.1 Top 10 root causes, ranked by issues likely closed ÷ effort",
        "bug | 578",
        "- 937 open issues were classified.",
        "  - [B] guards and CI gates that pass without checking (132)",
        "GET /repos/mrveiss/AutoBot-AI/issues?labels=<label>&state=open&per_page=100&page=N",
        "(https://docs.example.org/reports/dedup-defork-report.md)",
    ):
        assert kept in body, kept


@pytest.mark.asyncio
async def test_crawled_page_fact_body_is_the_document_only():
    connector = WebCrawlerConnector(
        ConnectorConfig(connector_id="c", connector_type="web_crawler", name="c", config={"urls": [_PAGE_URL]})
    )
    with patch.object(WebFetcher, "fetch_raw_html", AsyncMock(return_value=(_page_html(), 200))):
        result, raw_html = await connector._fetch_page_with_html(WebFetcher(), _PAGE_URL)
    content = _fetch_result_to_content(result, "c")
    assert content is not None and raw_html
    assert content.metadata["title"] == "AutoBot-AI: open bug & tech-debt issues, root-cause map"
    _assert_document_only(content.content)


@pytest.mark.asyncio
async def test_scraped_page_body_is_the_document_only():
    with (
        patch("web_fetch.fetcher._is_public_url", AsyncMock(return_value=True)),
        patch("web_fetch.fetcher.get_cached_result", AsyncMock(return_value=None)),
        patch("web_fetch.fetcher.set_cached_result", AsyncMock()),
        patch("web_fetch.fetcher._fetch_jina", AsyncMock(return_value=None)),
        patch("web_fetch.fetcher._fetch_bs4", AsyncMock(return_value=(_page_html(), 200))),
    ):
        result = await WebFetcher.fetch(_PAGE_URL, render=RenderMode.FAST)
    assert result.success and result.source == "bs4"
    _assert_document_only(result.markdown)
