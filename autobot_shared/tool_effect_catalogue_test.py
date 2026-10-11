# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Tool-effect facts are declared once and every consumer's tools exist in the catalogue (#18294)."""

import sys
from pathlib import Path

import pytest

from autobot_shared import repetition_guard as rg
from autobot_shared.tool_catalogue import (
    SENSITIVE_TOOLS,
    TOOL_ALIASES,
    TOOL_EFFECTS,
    ToolEffect,
    approval_gap_tools,
    canonical_tool_name,
    has_effect,
    match_tool_name,
    tool_effect,
    tools_with,
)

_BACKEND = str(Path(__file__).resolve().parents[1] / "autobot-backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def test_aliases_point_at_catalogued_tools_and_never_chain():
    for alias, canon in TOOL_ALIASES.items():
        assert canon in TOOL_EFFECTS, alias
        assert alias not in TOOL_EFFECTS, f"{alias} is both an alias and a canonical tool"


def test_an_aliased_spelling_classifies_like_its_canonical_tool():
    assert canonical_tool_name("delete_directory") == "remove_directory"
    assert tool_effect("delete_directory") == tool_effect("remove_directory")
    assert has_effect("list_dir", ToolEffect.READ)


def test_create_and_remove_directory_are_write_and_state():
    for tool in ("create_directory", "remove_directory", "delete_directory"):
        assert has_effect(tool, ToolEffect.WRITE | ToolEffect.STATE), tool
        assert has_effect(tool, ToolEffect.WRITE) and has_effect(tool, ToolEffect.STATE), tool


def test_unknown_tools_have_no_effect_rather_than_a_guess():
    assert tool_effect("no_such_tool") is None
    assert not has_effect("no_such_tool", ToolEffect.MUTATING)


def test_no_catalogued_tool_is_both_a_pure_read_and_mutating():
    for tool, effect in TOOL_EFFECTS.items():
        if effect & ToolEffect.READ:
            assert not effect & ToolEffect.MUTATING, tool


def test_every_sensitive_tool_has_a_mutating_effect():
    for tool in SENSITIVE_TOOLS:
        assert has_effect(tool, ToolEffect.MUTATING), f"{tool} is approval-gated but declared non-mutating"


def test_match_tool_name_resolves_an_alias_to_the_gated_tool():
    assert match_tool_name("delete_directory", SENSITIVE_TOOLS) == "remove_directory"
    assert match_tool_name("Delete_Directory", SENSITIVE_TOOLS) == "remove_directory"
    assert match_tool_name("list_dir", SENSITIVE_TOOLS) is None


#: Mutating tools the approval plane does not gate. Pinned so closing or widening the gap is a
#: visible edit; changing SENSITIVE_TOOLS is an approval-policy decision, not part of #18294.
_APPROVAL_GAP = frozenset(
    {
        "click", "compose", "create_file", "delegate", "evaluate", "fill", "insert_database",
        "manage_service", "navigate", "rename_file", "run_shell", "select", "set_redis_value",
        "start_process", "stop_process",
    }
)  # fmt: skip


def test_the_approval_gap_is_exactly_the_documented_set():
    assert approval_gap_tools() == _APPROVAL_GAP


def test_repetition_guard_pollable_tools_derive_from_the_catalogue():
    assert rg.POLLABLE_TOOLS == tools_with(ToolEffect.POLL) == {"sleep", "wait", "poll", "check_status"}
    assert not any(has_effect(t, ToolEffect.MUTATING) for t in rg.POLLABLE_TOOLS)


@pytest.mark.parametrize("tool", ["sleep", "poll"])
def test_a_pollable_tool_is_never_halted_for_repetition(tool):
    state: dict = {}
    call = {"name": tool, "params": {}}
    assert all(rg.repetition_halt_reason(call, [], state, threshold=1) is None for _ in range(3))


def test_a_non_pollable_tool_is_still_halted():
    state: dict = {}
    call = {"name": "read_file", "params": {"p": 1}}
    assert rg.repetition_halt_reason(call, [], state, threshold=1) is not None


def test_sandbox_policy_derives_from_the_catalogue():
    from chat_workflow.code_exec import tool_policy as tp

    assert tp.SENSITIVE_TOOLS == tools_with(ToolEffect.MUTATING)
    legacy_sensitive = {
        "execute_command", "compose", "delegate", "deploy", "git_push", "navigate", "click",
        "fill", "select", "evaluate", "write_file", "edit_file", "delete_file",
    }  # fmt: skip
    assert legacy_sensitive <= tp.SENSITIVE_TOOLS, "a pre-#18294 sensitive tool lost its class"
    assert tp.CODEEXEC_READONLY_TOOLS == tools_with(ToolEffect.ISOLATED_READ)
    assert not tp.SENSITIVE_TOOLS & tp.CODEEXEC_INJECTABLE_TOOLS


def test_every_tool_the_analyzer_references_is_catalogued():
    from tools.parallel.analyzer import DependencyAnalyzer

    for tool in DependencyAnalyzer.RESOURCE_EXTRACTORS:
        assert tool_effect(tool) is not None, f"analyzer extracts a resource for uncatalogued {tool!r}"
    for tool in ("create_directory", "execute_command"):  # names _might_depend_on_state special-cases
        assert tool_effect(tool) is not None


def test_every_tool_the_sandbox_policy_and_guard_reference_is_catalogued():
    from chat_workflow.code_exec import tool_policy as tp

    named = tp.SENSITIVE_TOOLS | tp.CODEEXEC_INJECTABLE_TOOLS | tp.CODEEXEC_READONLY_TOOLS | rg.POLLABLE_TOOLS
    assert named, "empty reference set"
    assert all(tool_effect(t) is not None for t in named), sorted(t for t in named if tool_effect(t) is None)
