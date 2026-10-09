# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""One ServiceRegistry for the infrastructure deployment scripts (#18132).

Six scripts under `autobot-infrastructure/shared/scripts` each carried their own
trimmed copy of one class. #18132 replaced them with `deployment_registry.py`. This
guard keeps the count at one: a seventh copy pasted back into a script fails here.

The detector reads the syntax tree, so a comment or docstring NAMING the class is not
a definition -- which matters, because every migrated script still says where its
registry now lives.

Out of scope, and not pinned here: the backend's own
`autobot-backend/utils/service_registry.py` and the SLM orchestrator's registry are
separate classes outside the infrastructure tree (sighting recorded on #12771).
"""

from __future__ import annotations

import ast
import functools

import pytest
from repo_tests._paths import repo_root

from tools.lint._scan_helpers import tracked_paths

_TREE = "autobot-infrastructure/"
_CANONICAL = "autobot-infrastructure/shared/scripts/deployment_registry.py"

#: The six scripts that held a copy before #18132 and must now import the shared one.
_CONSUMERS = tuple(
    f"autobot-infrastructure/shared/scripts/{name}.py"
    for name in (
        "backup_manager",
        "deploy_autobot",
        "deployment_rollback",
        "log_aggregator",
        "metrics_collector",
        "zero_downtime_deploy",
    )
)


def _defines_registry(source: str) -> bool:
    """True when `source` declares a class named ServiceRegistry at any depth."""
    return any(
        isinstance(node, ast.ClassDef) and node.name == "ServiceRegistry" for node in ast.walk(ast.parse(source))
    )


def _imports_shared_registry(source: str) -> bool:
    """True when `source` takes ServiceRegistry from the sibling deployment_registry."""
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "deployment_registry"
        and any(alias.name == "ServiceRegistry" for alias in node.names)
        for node in ast.walk(ast.parse(source))
    )


def _read(rel: str) -> str:
    return (repo_root() / rel).read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def _definers() -> tuple[tuple[str, ...], int]:
    """Files under the infrastructure tree defining ServiceRegistry, and how many parsed."""
    found: list[str] = []
    parsed = 0
    for rel in tracked_paths(repo_root(), _TREE):
        if not rel.endswith(".py"):
            continue
        source = _read(rel)
        if "ServiceRegistry" not in source:
            continue
        parsed += 1
        if _defines_registry(source):
            found.append(rel)
    return tuple(found), parsed


@pytest.mark.parametrize(
    ("label", "source", "expected"),
    [
        ("a class definition", "class ServiceRegistry:\n    pass\n", True),
        ("a nested definition", "def f():\n    class ServiceRegistry:\n        pass\n", True),
        ("an import", "from deployment_registry import ServiceRegistry\n", False),
        ("a comment naming it", "# ServiceRegistry lives in deployment_registry.py\nx = 1\n", False),
    ],
)
def test_the_matcher_tells_a_definition_from_a_mention(label: str, source: str, expected: bool) -> None:
    assert _defines_registry(source) is expected, label


def test_the_scan_is_not_vacuous() -> None:
    """Every consumer mentions the class, so a scan parsing fewer has looked elsewhere."""
    _, parsed = _definers()
    assert parsed >= len(_CONSUMERS) + 1, f"parsed {parsed} files mentioning ServiceRegistry"


def test_the_infrastructure_tree_has_one_service_registry() -> None:
    definers, _ = _definers()
    assert definers == (_CANONICAL,), (
        f"#18132: ServiceRegistry is defined in {list(definers)}; the deployment scripts "
        f"share one class in {_CANONICAL}. Import it from there instead of copying it."
    )


@pytest.mark.parametrize("rel", _CONSUMERS)
def test_each_former_copy_imports_the_shared_registry(rel: str) -> None:
    assert _imports_shared_registry(
        _read(rel)
    ), f"#18132: {rel} no longer imports ServiceRegistry from deployment_registry"


def test_service_status_is_the_canonical_health_enum() -> None:
    """#6973: the scripts' status type is an alias of HealthStatus, never a fresh Enum."""
    tree = ast.parse(_read(_CANONICAL))
    aliases = [
        node.value.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Name)
        and any(isinstance(t, ast.Name) and t.id == "ServiceStatus" for t in node.targets)
    ]
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "ServiceStatus"]
    assert aliases == ["HealthStatus"] and not classes, (aliases, len(classes))
