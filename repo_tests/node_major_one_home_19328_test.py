# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The Node.js major has ONE home: the repo-root ``.nvmrc`` (#19328, maintainer decision 2026-10-26: 22).

CI tested on 22 while every install path shipped 20 (end-of-life 2026-04-30) and the execution
sandbox ran 18. Now:

* the workflows read it (``actions/setup-node`` with ``node-version-file: .nvmrc``);
* consumers that cannot read the repo file at run time -- the three shell installers (they can run
  from a ``curl | bash`` pipe), Ansible on a node (``inventory/group_vars/all.yml`` ``node_major``,
  which every role and playbook references), the two Dockerfiles' ``ARG NODE_MAJOR`` and the
  sandbox image -- carry ONE pinned copy each, and this guard fails the moment one disagrees;
* the frontend ``engines`` floor sits on the same major.

The sweep also fails on any literal NodeSource ``setup_NN.x`` / ``node_NN.x`` or a hard-coded
``nodejs_version`` in the workflows, install scripts and Ansible tree: those read the variable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare

_ROOT = repo_root()
_ROOTS = (".github", "autobot-slm-backend/ansible", "autobot-infrastructure/shared/scripts", "install.sh")
_SUFFIXES = (".yml", ".yaml", ".sh", ".j2")
_LITERAL = re.compile(
    r"setup_\d+\.x|deb\.nodesource\.com/node_\d+\.x|nodejs(?:_major)?_version:\s*['\"]?\d|^\s*node-version:\s|^\s*NODE_VERSION:",
    re.MULTILINE,
)

#: (file, pattern whose group 1 is the pinned major) -- each consumer that cannot read .nvmrc.
_PINNED = (
    ("install.sh", r"\$\{NODE_MAJOR:=(\d+)\}"),
    ("autobot-infrastructure/shared/scripts/install-bare-metal.sh", r"\$\{NODE_MAJOR:=(\d+)\}"),
    ("autobot-infrastructure/shared/scripts/install-slm.sh", r"\$\{NODE_MAJOR:=(\d+)\}"),
    ("autobot-slm-backend/ansible/inventory/group_vars/all.yml", r"^node_major:\s*\"(\d+)\"$"),
    ("docker/frontend/Dockerfile", r"^ARG NODE_MAJOR=(\d+)$"),
    ("autobot-infrastructure/autobot-frontend/docker/frontend.Dockerfile", r"^ARG NODE_MAJOR=(\d+)$"),
    ("autobot-backend/services/execution/docker_backend.py", r"\"node:(\d+)-slim\""),
)


def node_major(root: Path = _ROOT) -> int:
    return int((root / ".nvmrc").read_text(encoding="utf-8").strip().lstrip("v").split(".")[0])


def _sources(root: Path) -> list[Path]:
    found = []
    for top in _ROOTS:
        base = root / top
        if base.is_file():
            found.append(base)
        elif base.is_dir():
            found += [p for p in sorted(base.rglob("*")) if p.suffix in _SUFFIXES and "_test" not in p.name]
    return found


REACH = declare(
    "node-major-literals",
    discover=_sources,
    floor=611,  # mid-window: population 641, window (581, 641)
    growth=60,  # ~9%: ordinary growth between repins must not turn an unrelated PR red
    what="workflow, Ansible and install-script files",
    roots=_ROOTS,
)


def literals(text: str, rel: str) -> list[str]:
    return [f"{rel}: {m.group(0).strip()}" for m in _LITERAL.finditer(text)]


def test_the_home_is_the_decided_major() -> None:
    assert node_major() == 22, "maintainer decision 2026-10-26 (#19328): Node 22 everywhere"


@pytest.mark.parametrize("rel,pattern", _PINNED)
def test_every_pinned_copy_equals_the_home(rel: str, pattern: str) -> None:
    found = re.findall(pattern, (_ROOT / rel).read_text(encoding="utf-8"), re.MULTILINE)
    assert len(found) == 1, f"{rel}: expected one pinned Node major, found {found}"
    assert int(found[0]) == node_major(), f"{rel} pins Node {found[0]}, .nvmrc says {node_major()}"


def test_the_frontend_engines_floor_is_on_the_home_major() -> None:
    engines = json.loads((_ROOT / "autobot-frontend/package.json").read_text(encoding="utf-8"))["engines"]["node"]
    assert int(re.search(r"\d+", engines).group(0)) == node_major(), engines


def test_no_literal_node_version_in_workflows_ansible_or_installers() -> None:
    files = REACH.examined(_ROOT)
    found = [h for p in files for h in literals(p.read_text(encoding="utf-8"), p.relative_to(_ROOT).as_posix())]
    REACH.completed(files)
    assert not found, f"read .nvmrc (workflows) or node_major / NODE_MAJOR (#19328): {found}"


@pytest.mark.parametrize(
    "text",
    [
        "        node-version: '20'\n",
        "  NODE_VERSION: '20'\n",
        "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -\n",
        'repo: "deb https://deb.nodesource.com/node_20.x jammy main"\n',
        'nodejs_version: "20"\n',
    ],
)
def test_each_literal_shape_is_flagged(text: str) -> None:
    assert literals(text, "x")


def test_the_derived_forms_are_not_flagged() -> None:
    clean = (
        "        node-version-file: .nvmrc\n"
        "curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash -\n"
        'repo: "deb https://deb.nodesource.com/node_{{ node_major }}.x jammy main"\n'
        'nodejs_version: "{{ node_major }}"\n'
    )
    assert not literals(clean, "x")
