# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Exact find-and-replace for the filesystem MCP ``edit_file`` tool (#19463).

Every edit must match exactly ``expected_replacements`` times (default 1). A miss
or a count mismatch raises a 400 naming the edit, and for a miss the closest span
in the file (difflib similarity, line range, short unified diff) so the model can
retry with the exact text. A fuzzy match is never applied. Edits run against a
working copy, so a multi-edit call is all-or-nothing: the caller writes only when
every edit succeeded. A file whose every line ends CRLF is matched LF-normalised
and written back CRLF; any other file is matched as-is.
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass
from typing import List, Tuple

from autobot_shared.logging_manager import get_logger
from autobot_shared.secret_redaction import redact_mapping
from autobot_shared.security.redaction import sanitize_log_value
from utils.catalog_http_exceptions import raise_invalid_input

logger = get_logger(__name__)

CRLF = "\r\n"
LF = "\n"
#: Lines of unified diff returned with a miss: enough to show the misquote, short enough for a model's context.
MISS_DIFF_MAX_LINES = 20
#: Longest file extension written to the edit-miss log event.
_EXTENSION_LOG_MAX_CHARS = 16


@dataclass(frozen=True)
class ClosestSpan:
    """The file span most similar to a missed ``old_text`` (1-based, inclusive)."""

    similarity: float
    start_line: int
    end_line: int
    text: str


def detect_line_ending(content: str) -> str:
    """Return CRLF when every newline in *content* is CRLF, else LF."""
    lf_count = content.count(LF)
    if lf_count and content.count(CRLF) == lf_count:
        return CRLF
    return LF


def find_closest_span(content: str, old_text: str) -> ClosestSpan:
    """Slide an ``old_text``-sized line window over *content*; return the best match."""
    lines = content.split(LF)
    width = max(1, min(len(lines), old_text.count(LF) + 1))
    matcher = difflib.SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(old_text)
    best = ClosestSpan(0.0, 1, width, LF.join(lines[:width]))
    for start in range(len(lines) - width + 1):
        candidate = LF.join(lines[start : start + width])
        matcher.set_seq1(candidate)
        if matcher.real_quick_ratio() <= best.similarity or matcher.quick_ratio() <= best.similarity:
            continue
        ratio = matcher.ratio()
        if ratio > best.similarity:
            best = ClosestSpan(ratio, start + 1, start + width, candidate)
    return best


def _short_diff(span: ClosestSpan, old_text: str) -> str:
    """Unified diff from the file span to ``old_text``, capped at MISS_DIFF_MAX_LINES."""
    diff = list(
        difflib.unified_diff(
            span.text.split(LF),
            old_text.split(LF),
            fromfile=f"file lines {span.start_line}-{span.end_line}",
            tofile="old_text",
            lineterm="",
            n=1,
        )
    )
    if len(diff) > MISS_DIFF_MAX_LINES:
        diff = diff[:MISS_DIFF_MAX_LINES] + [f"... [{len(diff) - MISS_DIFF_MAX_LINES} more diff lines]"]
    return LF.join(diff)


def _log_edit_miss(path: str, index: int, old_text: str, content: str, **fields) -> None:
    """Emit one structured edit-miss event: shapes and scores only, never content."""
    extension = os.path.splitext(path)[1].lower()
    event = {
        "event": "edit_file.edit_miss",
        "extension": sanitize_log_value(extension, max_chars=_EXTENSION_LOG_MAX_CHARS),
        "edit_index": index,
        "old_text_len": len(old_text),
        "file_len": len(content),
        **fields,
    }
    safe_event = redact_mapping(event)
    logger.warning("edit_file edit miss: %s", safe_event, extra={"edit_miss": safe_event})


def _expected_count(edit: dict, index: int) -> int:
    """Validate the optional ``expected_replacements`` of one edit (default 1)."""
    expected = edit.get("expected_replacements", 1)
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        raise_invalid_input("edits", f"edit {index}: expected_replacements must be an integer >= 1")
    return expected


def _edit_texts(edit: dict, index: int, line_ending: str) -> Tuple[str, str]:
    """Return one edit's (old_text, new_text), LF-normalised for a CRLF file."""
    old_text = edit.get("old_text", edit.get("oldText"))
    new_text = edit.get("new_text", edit.get("newText"))
    if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
        raise_invalid_input("edits", f"edit {index}: old_text must be a non-empty string and new_text a string")
    if line_ending == CRLF:
        return old_text.replace(CRLF, LF), new_text.replace(CRLF, LF)
    return old_text, new_text


def _raise_miss(path: str, index: int, old_text: str, content: str) -> None:
    """Report a missed edit with its closest span; nothing is applied."""
    span = find_closest_span(content, old_text)
    _log_edit_miss(path, index, old_text, content, reason="not_found", similarity=round(span.similarity, 3))
    raise_invalid_input(
        "edits",
        f"edit {index}: old_text not found; closest span is lines {span.start_line}-{span.end_line} "
        f"(similarity {span.similarity:.2f}). No edits were applied; retry with the exact text.\n"
        f"{_short_diff(span, old_text)}",
    )


def _apply_one(content: str, edit: dict, index: int, path: str, line_ending: str) -> Tuple[str, dict]:
    """Apply one exact edit to *content* or raise; returns (content, change record)."""
    old_text, new_text = _edit_texts(edit, index, line_ending)
    expected = _expected_count(edit, index)
    count = content.count(old_text)
    if count == 0:
        _raise_miss(path, index, old_text, content)
    if count != expected:
        _log_edit_miss(path, index, old_text, content, reason="count_mismatch", occurrences=count, expected=expected)
        raise_invalid_input(
            "edits",
            f"edit {index}: old_text occurs {count} times but expected_replacements is {expected}. "
            "No edits were applied; add surrounding context or set expected_replacements.",
        )
    change = {"old": old_text[:50], "new": new_text[:50], "replacements": count}
    return content.replace(old_text, new_text), change


def apply_edits_to_content(content: str, edits: list, path: str) -> Tuple[str, List[dict]]:
    """Apply every edit exactly or raise before any is kept (all-or-nothing).

    *content* is the file as read with ``newline=''``; the result keeps its
    line-ending style, so the caller writes it back with ``newline=''``.
    """
    line_ending = detect_line_ending(content)
    working = content.replace(CRLF, LF) if line_ending == CRLF else content
    changes = []
    for index, edit in enumerate(edits):
        working, change = _apply_one(working, edit, index, path, line_ending)
        changes.append(change)
    return (working.replace(LF, CRLF) if line_ending == CRLF else working), changes
