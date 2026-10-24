# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Split a shell command line into the commands it runs (#19462).

Risk assessment and learned auto-approve rules must judge every command a line
runs, not only the first one: ``cat x | sh`` is two commands, and naming it by
``cat`` alone rated it SAFE. This is the one splitter both use.

Separators are recognised outside quotes only: ``;``, ``&&``, ``||``, ``|``,
``|&``, ``&`` and newline. A ``&`` or ``|`` that belongs to a redirection
(``2>&1``, ``&>``, ``<&``, ``>|``) is not a separator. Command substitution is
not parsed here; ``security.command_patterns`` forbids it outright.

An unterminated quote never makes the scanner give up: separators before the
quote still split, and the rest of the line stays in the last segment, so a
trailing stray quote cannot hide the commands ahead of it.
"""

from __future__ import annotations

from typing import AbstractSet, List, Tuple

#: Longest first, so ``&&`` is never read as two ``&``.
_OPERATORS = ("&&", "||", "|&", ";", "|", "&", "\n")


def _operator_at(command: str, i: int) -> str:
    """The separator starting at ``command[i]`` outside quotes, or ``""``."""
    prev = command[i - 1] if i > 0 else ""
    nxt = command[i + 1] if i + 1 < len(command) else ""
    for op in _OPERATORS:
        if not command.startswith(op, i):
            continue
        if op == "&" and (prev in ("<", ">") or nxt == ">"):
            return ""  # 2>&1, <&3, &>file: a redirection, not a separator
        if op == "|" and prev == ">":
            return ""  # >|file: clobbering redirection
        return op
    return ""


def _quoted_end(command: str, i: int, escapes: bool = False) -> int:
    """Index just past the quoted run opening at ``command[i]`` (or the end).

    ``escapes``: a backslash escapes inside it -- always for ``"``, and for
    ``'`` only in ANSI-C ``$'...'``, where ``\\'`` does not close the string.
    """
    quote = command[i]
    j = i + 1
    while j < len(command):
        if (quote == '"' or escapes) and command[j] == "\\":
            j += 2
            continue
        if command[j] == quote:
            return j + 1
        j += 1
    return len(command)


def _skip_literal(command: str, i: int) -> int | None:
    """Index past a quote, an escape or a comment at ``command[i]``; else ``None``.

    A comment runs to the newline, which is left to split: a quote inside a
    comment is not a quote to the shell, so ``ls #'`` + newline + ``sh`` runs ``sh``.
    """
    ch = command[i]
    if ch == "$" and command.startswith("$'", i):
        return _quoted_end(command, i + 1, escapes=True)
    if ch in "'\"":
        return _quoted_end(command, i)
    if ch == "\\":
        return i + 2
    if ch == "#" and (i == 0 or command[i - 1] in " \t\n;&|()"):
        newline = command.find("\n", i)
        return len(command) if newline == -1 else newline
    return None


def split_command_line(command: str) -> List[Tuple[str, str]]:
    """``(segment, operator_after_it)`` pairs; the last operator is ``""``.

    Empty segments (``ls;``, ``a;;b``) are dropped, keeping the operator of the
    segment before them.
    """
    pairs: List[Tuple[str, str]] = []
    start = i = 0
    while i < len(command):
        skipped = _skip_literal(command, i)
        if skipped is not None:
            i = skipped
            continue
        op = _operator_at(command, i)
        if not op:
            i += 1
            continue
        _append(pairs, command[start:i], op)
        start = i = i + len(op)
    _append(pairs, command[start:], "")
    return pairs


#: Words that wrap a command without being it: ``(cmd)``, ``{ cmd; }``, ``! cmd``, ``time cmd``.
_LEADING_WRAPPERS = ("(", "{", "!", "time ")


def _unwrapped(segment: str) -> str:
    """``segment`` without the grouping and prefix words around its command."""
    segment = segment.strip()
    while True:
        wrapper = next((w for w in _LEADING_WRAPPERS if segment.startswith(w)), None)
        if wrapper is None:
            break
        segment = segment[len(wrapper) :].lstrip()
    return _without_unbalanced_closers(segment)


def _without_unbalanced_closers(segment: str) -> str:
    """Drop trailing ``)``/``}`` left by an opener on another segment; ``<(sh)`` keeps its own."""
    while segment and segment[-1] in ")}":
        closer, opener = segment[-1], "(" if segment[-1] == ")" else "{"
        if segment.count(closer) <= segment.count(opener):
            break
        segment = segment[:-1].rstrip()
    return segment


def _append(pairs: List[Tuple[str, str]], segment: str, op: str) -> None:
    """Add a non-empty segment; an empty one passes its operator to the last."""
    segment = _unwrapped(segment)
    if segment:
        pairs.append((segment, op))
    elif pairs and op:
        pairs[-1] = (pairs[-1][0], op)


def command_segments(command: str) -> List[str]:
    """The commands ``command`` runs, in order (one entry for a plain command)."""
    return [segment for segment, _op in split_command_line(command)]


def single_command_pattern(command: str, subcommand_tools: AbstractSet[str]) -> str:
    """Pattern for one command: ``ls -la`` → ``ls *``, ``git status x`` → ``git status *``."""
    parts = command.split()
    if not parts:
        return command
    base_cmd = parts[0]
    if len(parts) == 1:
        return base_cmd
    # Issue #326: tools with subcommands keep the subcommand in the pattern
    if base_cmd in subcommand_tools:
        return f"{base_cmd} {parts[1]} *"
    return f"{base_cmd} *"


def command_line_pattern(command: str, subcommand_tools: AbstractSet[str]) -> str:
    """Auto-approve pattern for a whole line: one pattern per segment, operators kept.

    A plain command keeps its historical pattern, so stored rules still match.
    ``cat a && sh`` → ``cat * && sh`` and ``cat a &`` → ``cat * &``, which a
    stored ``cat *`` rule cannot match.
    """
    pairs = split_command_line(command)
    if not pairs:
        return command
    if pairs == [(command, "")]:
        return single_command_pattern(command, subcommand_tools)
    return " ".join(f"{single_command_pattern(seg, subcommand_tools)} {op}".strip() for seg, op in pairs)
