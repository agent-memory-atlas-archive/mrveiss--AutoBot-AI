# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Behaviour of the canonical file-type sets (#19339)."""

import pytest

from utils.document_extractors import DocumentExtractor
from utils.file_categorization import HTML_EXTENSIONS
from utils.file_types import DOCX_EXTENSIONS, TEXT_FILE_EXTENSIONS


def test_legacy_doc_is_not_a_docx() -> None:
    assert DOCX_EXTENSIONS == frozenset({".docx"})
    assert ".doc" not in DocumentExtractor.SUPPORTED_FORMATS["docx"]


@pytest.fixture
def legacy_doc(tmp_path):
    path = tmp_path / "old.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0 not a zip")
    return path


@pytest.mark.asyncio
async def test_a_doc_upload_is_refused_as_an_unsupported_format(legacy_doc) -> None:
    with pytest.raises(ValueError, match="Unsupported file type: .doc"):
        await DocumentExtractor.extract_from_file(legacy_doc)


def test_the_extractor_html_formats_are_the_canonical_set() -> None:
    assert set(DocumentExtractor.SUPPORTED_FORMATS["html"]) == HTML_EXTENSIONS


def test_the_text_set_covers_every_extension_the_three_old_copies_accepted() -> None:
    assert {".csv", ".json", ".htm", ".rst", ".xml", ".kt", ".tsx", ".md", ".yaml"} <= TEXT_FILE_EXTENSIONS
