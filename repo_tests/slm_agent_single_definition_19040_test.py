# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""`class SLMAgent` is defined once, in autobot-slm-backend/slm/agent/ (#19040, #18548).

Four copies once drifted from the canonical agent: a mirror under the role's files/ (#18548), a
standalone script, and two playbooks that embedded the class in a `content:` block. Each wrote a
heartbeat the nodes router answered with 401. The role now deploys the one source tree, so a
definition anywhere else is a fork.

Python files are matched by AST (a docstring or string that merely names the class is not a
definition); YAML and Jinja are matched as text, because the class lives inside a `content:`
scalar there. A `.py` file that does not parse falls back to the text match rather than being
skipped.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from repo_tests._paths import repo_root
from repo_tests._reach import declare

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

_ROOT = repo_root()
_CANONICAL = "autobot-slm-backend/slm/agent/"
_CLASS = "SLMAgent"
_TEXT_DEFINITION = re.compile(rf"^\s*class\s+{_CLASS}\b", re.MULTILINE)
_SUFFIXES = (".py", ".yml", ".yaml", ".j2")
_SKIP_PARTS = frozenset({"__pycache__", "node_modules", ".worktrees", "venv", ".venv"})


def _candidates(root: Path) -> list[str]:
    try:
        paths = tracked_paths(root, *(f"*{suffix}" for suffix in _SUFFIXES))
    except EmptyEnumeration:
        return []
    # Skip dirs are matched on the repo-RELATIVE parts: a checkout that itself lives under
    # `.worktrees` must not skip every file and read as clean (#18972).
    return [p for p in paths if not _SKIP_PARTS & set(Path(p).parts)]


REACH = declare(
    "slm-agent-single-definition-19040",
    discover=_candidates,
    floor=7612,  # mid-window: population 7813 (py 7163, yml 471, yaml 70, j2 109), window [7413, 7813]
    growth=400,
    what="tracked .py/.yml/.yaml/.j2 files",
)


def defines_slm_agent(rel: str, text: str) -> bool:
    """True when *text*, the content of repo-relative *rel*, defines `class SLMAgent`."""
    if rel.endswith(".py"):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return bool(_TEXT_DEFINITION.search(text))
        return any(isinstance(n, ast.ClassDef) and n.name == _CLASS for n in ast.walk(tree))
    return bool(_TEXT_DEFINITION.search(text))


def offenders(files: dict[str, str]) -> list[str]:
    """Paths outside the canonical directory that define the class."""
    return sorted(rel for rel, text in files.items() if not rel.startswith(_CANONICAL) and defines_slm_agent(rel, text))


def test_no_file_outside_the_canonical_tree_defines_slm_agent() -> None:
    files = {}
    for rel in REACH.examined(_ROOT):
        try:
            files[rel] = (_ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary asset; carries no class definition
    assert offenders(files) == [], f"a second SLMAgent definition forks the agent; deploy {_CANONICAL} instead"


def test_the_canonical_definition_is_found_and_not_flagged() -> None:
    canonical = (_ROOT / _CANONICAL / "agent.py").read_text(encoding="utf-8")
    assert defines_slm_agent(f"{_CANONICAL}agent.py", canonical), "the detector no longer sees the real class"
    assert offenders({f"{_CANONICAL}agent.py": canonical}) == []


def test_a_planted_copy_is_caught_in_every_form() -> None:
    py = "class SLMAgent:\n    pass\n"
    nested = "def build():\n    class SLMAgent(object):\n        pass\n"
    yml = "- copy:\n    content: |\n      class SLMAgent:\n          pass\n"
    j2 = "# {{ managed }}\nclass SLMAgent:\n    pass\n"
    unparsable = "class SLMAgent(:\n"
    planted = {
        "scripts/a.py": py,
        "scripts/b.py": nested,
        "ansible/c.yml": yml,
        "ansible/d.yaml": yml,
        "ansible/e.py.j2": j2,
        "scripts/f.py": unparsable,
    }
    assert offenders(planted) == sorted(planted)


def test_a_mention_is_not_a_definition() -> None:
    mention = '"""Replaces the class SLMAgent copy."""\nOTHER = "class SLMAgent:"\nclass SLMAgentHelper:\n    pass\n'
    assert offenders({"scripts/g.py": mention, "docs/h.yml": "# see class SLMAgentHelper\n"}) == []
