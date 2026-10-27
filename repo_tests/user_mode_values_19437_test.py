# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Tenancy is ``AUTOBOT_USER_MODE``; ``AUTOBOT_DEPLOYMENT_MODE`` is topology (#19437).

The user guide told operators to set ``AUTOBOT_DEPLOYMENT_MODE=single_user`` / ``=multi_user``.
The code reads tenancy from ``AUTOBOT_USER_MODE`` (``UserManagementMode``), so following the
docs changed nothing, and wrote an unrecognised value into the topology setting (#19187 class).

The sweep covers docs and deployment files (Markdown, env files, compose, Ansible YAML and
templates). It fails on a tenancy value assigned to ``AUTOBOT_DEPLOYMENT_MODE``, and on an
``AUTOBOT_USER_MODE`` value that is not a ``UserManagementMode`` member. Templated values
(``{{ ... }}``, ``${...}``) and comment lines are not assignments.
"""

from __future__ import annotations

import ast
import re

from repo_tests._paths import repo_root

from tools.lint._scan_helpers import tracked_paths

_ROOT = repo_root()
_ENUM_FILE = "autobot-backend/user_management/config.py"
_PATTERNS = ("*.md", "*.env*", "*.yml", "*.yaml", "*.j2")
#: The tenancy values the docs used before #10636 retired the single-user mode.
_RETIRED = frozenset({"single_user", "multi_user"})
_ASSIGN = re.compile(r"\b(AUTOBOT_DEPLOYMENT_MODE|AUTOBOT_USER_MODE)\s*[=:]\s*[\"'`]?([A-Za-z_]+)")
#: 2000 tracked docs and deployment files at #19437 (`git ls-files` agrees).
MIN_FILES = 1500


def user_modes() -> frozenset[str]:
    """``UserManagementMode``'s values, read from its definition rather than restated."""
    tree = ast.parse((_ROOT / _ENUM_FILE).read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "UserManagementMode")
    return frozenset(n.value.value for n in cls.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant))


def misassignments(text: str, modes: frozenset[str]) -> list[str]:
    """Each ``line: VAR=value`` in *text* that puts a value in the wrong variable or set."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for var, value in _ASSIGN.findall(line):
            wrong_var = var == "AUTOBOT_DEPLOYMENT_MODE" and value in modes | _RETIRED
            wrong_value = var == "AUTOBOT_USER_MODE" and value not in modes
            if wrong_var or wrong_value:
                found.append(f"{number}: {var}={value}")
    return found


def test_no_doc_or_deployment_file_sets_tenancy_through_the_wrong_variable() -> None:
    paths = tracked_paths(_ROOT, *_PATTERNS)
    assert len(paths) >= MIN_FILES, f"only {len(paths)} files swept -- the enumeration lost its reach"
    modes = user_modes()
    bad = [f"{p}:{hit}" for p in paths for hit in misassignments((_ROOT / p).read_text(encoding="utf-8"), modes)]
    assert not bad, "tenancy is AUTOBOT_USER_MODE=single_company|multi_company|provider:\n" + "\n".join(bad)


def test_the_enum_is_read_not_assumed() -> None:
    assert user_modes() == {"single_company", "multi_company", "provider"}


def test_the_old_guide_wording_is_caught() -> None:
    modes = frozenset({"single_company", "multi_company", "provider"})
    text = (
        "This message appears when `AUTOBOT_DEPLOYMENT_MODE=single_user`.\n"
        "1. Set `AUTOBOT_DEPLOYMENT_MODE=multi_user` in your environment.\n"
        "Set `AUTOBOT_DEPLOYMENT_MODE=single_company` (or higher) to enable.\n"
        "AUTOBOT_USER_MODE=single_user\n"
    )
    assert len(misassignments(text, modes)) == 4


def test_valid_and_templated_assignments_are_not_findings() -> None:
    modes = frozenset({"single_company", "multi_company", "provider"})
    text = (
        "AUTOBOT_USER_MODE=multi_company\n"
        "AUTOBOT_DEPLOYMENT_MODE=distributed\n"
        "AUTOBOT_USER_MODE={{ backend_user_mode | default('single_company') }}\n"
        "      - AUTOBOT_USER_MODE=${AUTOBOT_USER_MODE:-single_company}\n"
        "# override.conf set AUTOBOT_USER_MODE=single_user, silently disabling it\n"
    )
    assert misassignments(text, modes) == []
