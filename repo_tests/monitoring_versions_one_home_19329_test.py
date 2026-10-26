# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Prometheus and Grafana versions have ONE home: the monitoring role's defaults (#19329).

Maintainer decision 2026-10-26: the compose values win -- Prometheus 2.53.0, Grafana 11.1.0.
Before, the VM fleet ran 2.48.0 / 10.2.0, compose ran 2.53.0 / 11.1.0, and two Ansible paths
installed Grafana ``latest``. CI already reads ``prometheus_version`` from the home to pin promtool.

Consumers that cannot read an Ansible role default -- the two compose stacks' image tags, the
``slm_manager`` role and the Grafana migration play -- carry ONE pinned copy each. This guard fails
when a copy differs from the home, when a version literal appears anywhere else in the compose,
infrastructure and Ansible trees, or when ``latest`` (or an untagged image) comes back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare

_ROOT = repo_root()
_HOME = "autobot-slm-backend/ansible/roles/monitoring/defaults/main.yml"
_ROOTS = ("docker-compose.yml", "autobot-infrastructure", "autobot-slm-backend/ansible")
_SUFFIXES = (".yml", ".yaml", ".ini")
_SITE = re.compile(
    r"(?P<tool>prometheus|grafana)_version\s*[:=]\s*[\"']?(?P<v>[\w.{}\s|]+?)[\"']?\s*(?:#.*)?$"
    r"|image:\s*(?:prom/(?P<itool>prometheus)|grafana/(?P<gtool>grafana))(?::v?(?P<iv>[\w.]+))?\s*$",
    re.MULTILINE,
)

#: Every file allowed to state a version, and how many statements it holds.
_PINNED = {
    _HOME: 2,
    "docker-compose.yml": 2,
    "autobot-infrastructure/shared/config/logging/loki/docker-compose-loki.yml": 1,
    "autobot-slm-backend/ansible/roles/slm_manager/defaults/main.yml": 1,
    "autobot-slm-backend/ansible/migrate-grafana-to-vm.yml": 1,
}


def _sources(root: Path) -> list[Path]:
    found = []
    for top in _ROOTS:
        base = root / top
        if base.is_file():
            found.append(base)
        elif base.is_dir():
            found += [p for p in sorted(base.rglob("*")) if p.suffix in _SUFFIXES]
    return found


REACH = declare(
    "monitoring-version-literals",
    discover=_sources,
    floor=384,  # mid-window: population 402, window (366, 402)
    growth=36,  # ~9%: ordinary growth between repins must not turn an unrelated PR red
    what="compose, infrastructure and Ansible yml/yaml/ini files",
    roots=_ROOTS,
)


def sites(text: str) -> list[tuple[str, str]]:
    """(tool, version) for each version statement in *text*; an untagged image reads as ``latest``."""
    found = []
    for m in _SITE.finditer(text):
        tool = m.group("tool") or m.group("itool") or m.group("gtool")
        found.append((tool, (m.group("v") or m.group("iv") or "latest").strip()))
    return found


def home() -> dict[str, str]:
    return dict(sites((_ROOT / _HOME).read_text(encoding="utf-8")))


def test_the_home_holds_the_decided_versions() -> None:
    assert home() == {"prometheus": "2.53.0", "grafana": "11.1.0"}, "maintainer decision 2026-10-26 (#19329)"


def test_every_statement_is_a_pinned_copy_equal_to_the_home() -> None:
    files = REACH.examined(_ROOT)
    found = {p.relative_to(_ROOT).as_posix(): sites(p.read_text(encoding="utf-8")) for p in files}
    REACH.completed(files)
    stated = {rel: s for rel, s in found.items() if s}
    assert {rel: len(s) for rel, s in stated.items()} == _PINNED, f"a copy outside the pinned set (#19329): {stated}"
    differ = [f"{rel}: {t} {v}" for rel, s in stated.items() for t, v in s if v != home()[t]]
    assert not differ, f"differs from {_HOME} {home()} (#19329): {differ}"


@pytest.mark.parametrize(
    "text,expected",
    [
        ('prometheus_version: "2.48.0"\n', [("prometheus", "2.48.0")]),
        ("grafana_version: latest\n", [("grafana", "latest")]),
        ("grafana_version=latest\n", [("grafana", "latest")]),
        ("    image: prom/prometheus:v2.53.0\n", [("prometheus", "2.53.0")]),
        ("    image: grafana/grafana\n", [("grafana", "latest")]),
        ('grafana_version: "11.1.0"  # pinned copy\n', [("grafana", "11.1.0")]),
    ],
)
def test_each_statement_shape_is_read(text: str, expected: list[tuple[str, str]]) -> None:
    assert sites(text) == expected


def test_references_are_not_statements() -> None:
    assert not sites('    name: "grafana={{ grafana_version }}"\n    release_version: "{{ prometheus_version }}"\n')
