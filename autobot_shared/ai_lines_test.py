# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The pure part of the three AI lines: vocabulary, keys and the resolution rule (#18430)."""

import pytest

from autobot_shared.ai_lines import (
    DEFAULT_LINE,
    SOURCE_BASELINE,
    SOURCE_DEFAULT,
    SOURCE_ROLE,
    SOURCE_STORED,
    SOURCE_UNAVAILABLE,
    SOURCE_UNMANAGED,
    AiLine,
    action_key,
    fallback_keys,
    is_mutating,
    parse_line,
    resolve_line,
    strictest,
)

L1, L2, L3 = AiLine.AUTONOMOUS, AiLine.DRAFT_THEN_SEND, AiLine.NEVER


def test_the_three_lines_are_ordered_loosest_to_strictest():
    assert [line.rank for line in (L1, L2, L3)] == [1, 2, 3]
    assert strictest(L1, L3, L2) is L3 and strictest(L1, L1) is L1


def test_an_unset_line_counts_as_line_two():
    assert DEFAULT_LINE is L2


def test_parse_line_never_guesses():
    assert parse_line("never") is L3
    assert [parse_line(v) for v in ("autonomous ", "NEVER", "", None, 3)] == [None] * 5


def test_a_tool_action_key_is_per_action_with_the_tool_as_fallback():
    assert action_key("crm", {"action": "Send"}) == "crm:send"
    assert action_key("crm", {"action": "  "}) == "crm"
    assert action_key("crm", None) == "crm" and action_key("crm", {"action": 7}) == "crm"
    assert action_key("delete_directory") == "remove_directory"  # the catalogue's canonical spelling
    assert fallback_keys("crm:send") == ("crm:send", "crm") and fallback_keys("crm") == ("crm",)


def test_only_catalogued_mutating_tools_count_as_mutating():
    assert is_mutating("write_file") and is_mutating("execute_command")
    assert not is_mutating("read_file") and not is_mutating("no_such_tool")


@pytest.mark.parametrize("stored", [L1, L2, L3])
def test_a_stored_line_wins_over_the_default(stored):
    got = resolve_line("write_file", stored=stored, mutating=True)
    assert got.line is stored and got.source == SOURCE_STORED


def test_unset_is_line_two_for_a_mutating_action_and_unmanaged_for_one_that_changes_nothing():
    mutating = resolve_line("write_file", stored=None, mutating=True)
    reading = resolve_line("read_file", stored=None, mutating=False)
    assert (mutating.line, mutating.source) == (L2, SOURCE_DEFAULT)
    assert (reading.line, reading.source) == (L1, SOURCE_UNMANAGED)


def test_a_baseline_category_can_never_be_line_one_whatever_is_stored():
    for stored in (L1, None):
        got = resolve_line("write_file", stored=stored, mutating=True, baseline_category="writing files")
        assert got.line is L2
    floored = resolve_line("write_file", stored=L1, mutating=True, baseline_category="writing files")
    assert floored.source == SOURCE_BASELINE and "writing files" in floored.reason


def test_the_baseline_only_tightens():
    never = resolve_line("write_file", stored=L3, mutating=True, baseline_category="writing files")
    two = resolve_line("write_file", stored=L2, mutating=True, baseline_category="writing files")
    assert (never.line, two.line) == (L3, L2) and two.source == SOURCE_STORED


def test_a_role_that_needs_approval_can_never_be_line_one_and_never_loosens():
    assert resolve_line("x", stored=L1, mutating=True, role_requires_approval=True).source == SOURCE_ROLE
    assert resolve_line("x", stored=L1, mutating=True, role_requires_approval=True).line is L2
    assert resolve_line("x", stored=L1, mutating=True, role_requires_approval=False).line is L1
    assert resolve_line("x", stored=L3, mutating=True, role_requires_approval=True).line is L3


def test_an_unreadable_store_fails_closed_to_never():
    got = resolve_line("x", stored=L1, mutating=False, store_unavailable=True)
    assert (got.line, got.source) == (L3, SOURCE_UNAVAILABLE)
