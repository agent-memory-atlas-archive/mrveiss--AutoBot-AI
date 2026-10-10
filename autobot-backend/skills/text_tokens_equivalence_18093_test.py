# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""`word_tokens` equals both previous copies on a corpus (#18093)."""

import re

import pytest

from autobot_shared.text_tokens import word_tokens

_CORPUS = [
    "",
    " ",
    "___",
    "analyze this PDF document",
    "Analyze_this PDF-document!",
    "snake_case and kebab-case",
    "Zürich Café 日本語 テキスト",
    "٣٤٥ digits 2² ½",
    "a..b//c\\d\te\nf",
    "UPPER lower MiXeD 123",
    "x" * 300,
    "tab\x00nul\x1besc",
]


def _old_prepared_facts(text):
    return frozenset(t for t in re.split(r"[\W_]+", text.lower()) if t)


def _old_skill_router(text):
    return {t for t in re.split(r"[\W_]+", text.lower()) if t}


@pytest.mark.parametrize("text", _CORPUS)
def test_equals_both_previous_copies(text):
    assert word_tokens(text) == _old_prepared_facts(text) == _old_skill_router(text)
    assert isinstance(word_tokens(text), frozenset)


def test_both_call_sites_use_the_shared_helper():
    from prepared_facts import _tok
    from skills.builtin.skill_router import _tokenize

    assert _tok is word_tokens
    assert _tokenize("Analyze_this PDF") == {"analyze", "this", "pdf"}
    assert isinstance(_tokenize("a b"), set) and not isinstance(_tokenize("a b"), frozenset)
