# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""HealthStatus carries CIRCUIT_OPEN and the backend registry status aliases it (#12771).

Also pins that every name `monitor_services.py` imports from `utils.service_registry`
is actually bound there (it imported a `ServiceStatus` that never existed).
"""

from __future__ import annotations

import ast

from repo_tests._paths import repo_root

from autobot_shared.status_enums import HealthStatus

_ROOT = repo_root()
_REGISTRY = _ROOT / "autobot-backend/utils/service_registry.py"
_MONITOR = _ROOT / "autobot-infrastructure/shared/scripts/monitor_services.py"


def _bound_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    return names


# The registry's former private enum, member by member: what the CLI printed via `.value`.
_FORMER_REGISTRY_MEMBERS = {
    "HEALTHY": "healthy",
    "UNHEALTHY": "unhealthy",
    "UNKNOWN": "unknown",
    "CIRCUIT_OPEN": "circuit_open",
}


def test_circuit_open_member_is_canonical() -> None:
    assert HealthStatus.CIRCUIT_OPEN.value == "circuit_open"


def test_four_registry_members_keep_name_and_value() -> None:
    """CLI/consumers see identical values before and after (#12771 decision 2)."""
    assert {n: HealthStatus[n].value for n in _FORMER_REGISTRY_MEMBERS} == _FORMER_REGISTRY_MEMBERS


def test_registry_status_is_an_alias_not_a_class() -> None:
    tree = ast.parse(_REGISTRY.read_text(encoding="utf-8"))
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RegistryServiceStatus"]
    aliases = [
        n.value.id
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Name)
        and any(isinstance(t, ast.Name) and t.id == "RegistryServiceStatus" for t in n.targets)
    ]
    assert not classes and aliases == ["HealthStatus"], (len(classes), aliases)


def test_monitor_services_imports_only_names_the_registry_binds() -> None:
    bound = _bound_names(ast.parse(_REGISTRY.read_text(encoding="utf-8")))
    tree = ast.parse(_MONITOR.read_text(encoding="utf-8"))
    wanted = {
        a.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module == "utils.service_registry"
        for a in n.names
    }
    assert wanted and not wanted - bound, sorted(wanted - bound)
