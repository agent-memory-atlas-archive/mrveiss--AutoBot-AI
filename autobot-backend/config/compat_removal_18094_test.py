# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""config.compat keeps only the browser-VM VNC URL; the rest is gone (#18094)."""

import ast
import importlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "autobot-infrastructure" / "shared" / "scripts"
_REMOVED = (
    "API_BASE_URL",
    "BACKEND_HOST_IP",
    "BACKEND_PORT",
    "HTTP_PROTOCOL",
    "OLLAMA_HOST_IP",
    "OLLAMA_PORT",
    "OLLAMA_URL",
    "REDIS_HOST_IP",
    "Config",
    "legacy_config",
)
_KEPT = ("PLAYWRIGHT_HOST_IP", "PLAYWRIGHT_VNC_PORT", "PLAYWRIGHT_VNC_URL", "get_vnc_direct_url")


def test_import_config_still_works():
    config = importlib.import_module("config")
    assert config.get_config_value is not None


@pytest.mark.parametrize("name", _REMOVED)
def test_removed_name_is_not_exported(name):
    config = importlib.import_module("config")
    assert not hasattr(config, name)
    assert name not in config.__all__


@pytest.mark.parametrize("name", _KEPT)
def test_vnc_names_still_resolve(name):
    config = importlib.import_module("config")
    assert getattr(config, name) is not None
    compat = importlib.import_module("config.compat")
    assert hasattr(compat, name)


def test_compat_module_has_no_wrapper_functions():
    compat = importlib.import_module("config.compat")
    leftovers = [
        n
        for n in ("Config", "get_config_value_from", "get_config_section_from", "load_config_async")
        if hasattr(compat, n)
    ]
    assert leftovers == []


def test_scripts_read_backend_and_ollama_urls_from_ssot():
    offenders = []
    for path in _SCRIPTS.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "config":
                names = {a.name for a in node.names}
                if names & {"API_BASE_URL", "OLLAMA_URL"}:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
