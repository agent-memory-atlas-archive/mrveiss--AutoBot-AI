# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""A pulled model is checked for the KIND its role needs, not just its name (#19736).

``_shared/tasks/assert_model_kinds.yml`` asks the model server (``ollama show`` -> Capabilities)
what each model can do. The embedding role's model must have ``embedding``, every other pulled
model ``completion``; a mismatch fails the install naming the model. These tests run the task's
real shell against a fake ``ollama`` that prints a chosen capability list.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from repo_tests._paths import repo_root

_ANS = "autobot-slm-backend/ansible"
_TASK = f"{_ANS}/_shared/tasks/assert_model_kinds.yml"
_SITES = (f"{_ANS}/roles/agent_config/tasks/ollama.yml", f"{_ANS}/playbooks/deploy-hybrid-docker.yml")

# What the fake CLI answers per model: a capability list, or None for "not installed".
_CAPS = {"embedder": ["embedding"], "chatty": ["completion", "tools"], "nocaps": []}


def _tasks(rel: str) -> list[dict]:
    """Every top-level item of every document (deploy-hybrid-docker.yml is multi-document)."""
    docs = yaml.safe_load_all((repo_root() / rel).read_text(encoding="utf-8"))
    return [item for doc in docs if doc for item in doc]


def _fake_ollama(bin_dir: Path) -> Path:
    lines = ["#!/bin/sh", '[ "$1" = show ] || exit 2', 'case "$2" in']
    for model, caps in _CAPS.items():
        body = "printf '  Model\\n    arch x\\n\\n'"
        if caps:
            body += "; printf '  Capabilities\\n" + "".join(f"    {c}    \\n" for c in caps) + "\\n'"
        lines.append(f"  {model}) {body} ;;")
    lines += ["  *) echo 'Error: model not found' >&2; exit 1 ;;", "esac"]
    cli = bin_dir / "ollama"
    cli.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    return cli


def _probe(tmp_path: Path, embedding: str, completion: list[str]) -> list[str]:
    cli = _fake_ollama(tmp_path)
    cmd = _tasks(_TASK)[0]["ansible.builtin.shell"]["cmd"]
    env = {
        **os.environ,
        "MODEL_CLI": str(cli),
        "EMBEDDING_MODEL": embedding,
        "COMPLETION_MODELS": "\n".join(completion),
    }
    out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=True, env=env)
    return out.stdout.split("\n")[:-1]


def test_the_right_kinds_pass(tmp_path: Path) -> None:
    assert _probe(tmp_path, "embedder", ["chatty"]) == ["OK embedder embedding", "OK chatty completion"]


def test_a_chat_model_in_the_embedding_role_is_the_wrong_kind(tmp_path: Path) -> None:
    assert _probe(tmp_path, "chatty", []) == ["WRONG-KIND chatty needs=embedding has=[completion tools]"]


def test_an_embedding_model_in_a_chat_role_is_the_wrong_kind(tmp_path: Path) -> None:
    assert _probe(tmp_path, "", ["embedder"]) == ["WRONG-KIND embedder needs=completion has=[embedding]"]


def test_the_embedding_model_in_the_pull_list_is_judged_once_as_an_embedder(tmp_path: Path) -> None:
    assert _probe(tmp_path, "embedder", ["chatty", "embedder"]) == ["OK embedder embedding", "OK chatty completion"]


@pytest.mark.parametrize(("model", "line"), [("ghost", "ABSENT ghost"), ("nocaps", "UNKNOWN nocaps")])
def test_an_undescribed_model_is_reported_not_judged(tmp_path: Path, model: str, line: str) -> None:
    assert _probe(tmp_path, "", [model]) == [line]


def test_the_install_fails_exactly_on_a_wrong_kind_verdict() -> None:
    fail = next(t for t in _tasks(_TASK) if "ansible.builtin.fail" in t)
    assert "'^WRONG-KIND '" in fail["when"]
    report = next(t for t in _tasks(_TASK) if "ansible.builtin.debug" in t)
    assert "ABSENT|UNKNOWN" in report["when"]


def _includes_check(node) -> list[dict]:
    if isinstance(node, list):
        return [inc for item in node for inc in _includes_check(item)]
    if not isinstance(node, dict):
        return []
    found = [node] if str(node.get("ansible.builtin.include_tasks", "")).endswith("assert_model_kinds.yml") else []
    for key in ("tasks", "block", "rescue", "always", "pre_tasks", "post_tasks"):
        found += _includes_check(node.get(key))
    return found


@pytest.mark.parametrize("site", _SITES)
def test_each_wired_pull_site_names_its_embedding_role(site: str) -> None:
    includes = _includes_check(_tasks(site))
    assert len(includes) == 1, f"{site} must run the kind check once after its pull"
    assert includes[0]["vars"]["model_kind_embedding"].startswith("{{ ")
    assert "model_kind_completion" in includes[0]["vars"]
