# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""FileServerConnector default excludes: canonical skip dirs and minified files (#19339)."""

from pathlib import Path

import pytest

from knowledge.connectors.file_server import FileServerConnector
from knowledge.connectors.models import ConnectorConfig


def _connector(base: Path, **extra) -> FileServerConnector:
    config = {"base_path": str(base), "include_patterns": ["*.py", "*.js"], **extra}
    return FileServerConnector(
        ConnectorConfig(connector_id="fs-19339", connector_type="file_server", name="fs", config=config)
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for rel in (
        "keep.py",
        "pkg/app.py",
        "node_modules/dep.js",
        "pkg/dist/bundle.js",
        ".worktrees/wt/copy.py",
        "web/app.min.js",
    ):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def _scanned_rel(connector: FileServerConnector, base: Path) -> set[str]:
    return {
        str(p.relative_to(base))
        for p in base.rglob("*")
        if p.is_file()
        and not connector._matches_exclude(str(p.relative_to(base)))
        and connector._matches_include(str(p.relative_to(base)))
    }


def test_default_excludes_skip_canonical_dirs_and_minified_files(tree: Path) -> None:
    assert _scanned_rel(_connector(tree), tree) == {"keep.py", "pkg/app.py"}


def test_an_explicit_exclude_list_replaces_the_defaults(tree: Path) -> None:
    got = _scanned_rel(_connector(tree, exclude_patterns=["pkg/*"]), tree)
    assert "node_modules/dep.js" in got and "web/app.min.js" in got and "pkg/app.py" not in got
