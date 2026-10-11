# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Three ansible settings that each had two owners, now one (#17856).

| Setting          | The one authority                                                        |
|------------------|--------------------------------------------------------------------------|
| backend_workers  | `roles/backend/defaults` (4) + `group_vars/slm.yml` (1); never a play-level vars file |
| ai_stack_port    | 8080 -- 11434 is the Ollama port, a different service                    |
| single-worker unit | none: the canonical backend unit renders with `backend_workers=1`      |

The detectors read text line by line against a contrast fixture, so prose that names a setting
without assigning it is not a definition.
"""

from __future__ import annotations

import functools
import re

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

_ANSIBLE = "autobot-slm-backend/ansible"
_AI_STACK_PORT = "8080"
_SUFFIXES = (".yml", ".yaml", ".j2", ".sh")

_WORKERS_KEY = re.compile(r"^backend_workers\s*:")
_AI_PORT_ASSIGN = re.compile(r"^\s*ai_stack_port\s*:\s*[\"']?(\d+)[\"']?\s*(?:#.*)?$|AUTOBOT_AI_STACK_PORT=(\d+)\s*$")
_SINGLE_UNIT_REF = re.compile(r"single-worker\.service\.j2")


def _defines_backend_workers(text: str) -> bool:
    """True when a vars file assigns `backend_workers` at its top level (comments don't count)."""
    return any(_WORKERS_KEY.match(line) for line in text.splitlines())


def _wrong_ai_stack_ports(text: str) -> list[str]:
    """Literal AI-stack port assignments that are not 8080."""
    bad = []
    for line in text.splitlines():
        m = _AI_PORT_ASSIGN.search(line)
        if m and (m.group(1) or m.group(2)) != _AI_STACK_PORT:
            bad.append(line.strip())
    return bad


def _references_single_worker_unit(text: str) -> bool:
    return any(_SINGLE_UNIT_REF.search(line) and not line.lstrip().startswith("#") for line in text.splitlines())


@pytest.mark.parametrize(
    ("probe", "fork", "clean"),
    [
        (
            _defines_backend_workers,
            "backend_user: a\nbackend_workers: 1  # #876\n",
            "# backend_workers is not set here\nx: 1\n",
        ),
        (
            lambda t: bool(_wrong_ai_stack_ports(t)),
            '    ai_stack_port: "11434"\n',
            '    ai_stack_port: "8080"\n# ollama_port: 11434\n',
        ),
        (lambda t: bool(_wrong_ai_stack_ports(t)), "AUTOBOT_AI_STACK_PORT=11434\n", "AUTOBOT_AI_STACK_PORT=8080\n"),
        (
            _references_single_worker_unit,
            "    src: templates/autobot-backend-single-worker.service.j2\n",
            "# the single-worker.service.j2 is retired\n",
        ),
    ],
)
def test_each_detector_tells_a_second_definition_from_prose(probe, fork: str, clean: str) -> None:
    assert probe(fork) is True
    assert probe(clean) is False


def _population(root) -> list[str]:
    try:
        tracked = tracked_paths(root, _ANSIBLE)
    except EmptyEnumeration:
        return []
    return [rel for rel in tracked if rel.endswith(_SUFFIXES)]


REACH = declare(
    "ansible-concept-forks-17856-scan",
    discover=_population,
    # Mid-window, from REACH.window(); the arithmetic is on the PR, not here.
    floor=438,
    what="ansible yaml, template and shell files",
    roots=(_ANSIBLE,),
    growth=30,
)


@functools.lru_cache(maxsize=1)
def _texts() -> dict[str, str]:
    root = repo_root()
    return {rel: (root / rel).read_text(encoding="utf-8", errors="replace") for rel in REACH.examined(root)}


def test_no_play_level_vars_file_sets_backend_workers() -> None:
    offenders = [r for r, t in _texts().items() if "/playbooks/vars/" in r and _defines_backend_workers(t)]
    assert (
        offenders == []
    ), f"#17856: {offenders} shadow roles/backend/defaults and group_vars/slm.yml; delete the line."


def test_every_literal_ai_stack_port_is_8080() -> None:
    offenders = {r: b for r, t in _texts().items() if (b := _wrong_ai_stack_ports(t))}
    assert offenders == {}, f"#17856: AI-stack port is {_AI_STACK_PORT} (11434 is Ollama): {offenders}"


def test_no_single_worker_unit_template_or_reference_remains() -> None:
    names = [r for r in _texts() if r.endswith("single-worker.service.j2")]
    refs = [r for r, t in _texts().items() if _references_single_worker_unit(t)]
    assert (names, refs) == ([], []), "#17856: render the canonical backend unit with backend_workers=1 instead."
