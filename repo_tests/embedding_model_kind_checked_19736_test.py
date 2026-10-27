# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""An update keeps a deployed embedding model only when it can embed (#19736).

#15502 keeps the deployed AUTOBOT_EMBEDDING_MODEL so an update never strands the index. That holds
for a model that embeds -- its vectors are the index. A model the model server reports cannot embed
(installs from before 2026-10-11 carry the light chat model there, #18834) never wrote a vector, so
keeping it only keeps the knowledge base broken. The read task asks the server named in the same env
file; "not-embedding" falls back to the canonical default, anything unanswerable keeps the value.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from repo_tests._paths import repo_root

_READ = "autobot-slm-backend/ansible/_shared/tasks/read_deployed_embedding_model.yml"
_KIND = "_embedding_kind_probe"
_NOT = "'not-embedding'"


def _tasks() -> list[dict]:
    return yaml.safe_load((repo_root() / _READ).read_text(encoding="utf-8"))


def _kind_task() -> dict:
    return next(t for t in _tasks() if t.get("register") == _KIND)


def _fake_curl(bin_dir: Path, body: str, log: Path) -> None:
    """A curl on PATH that records its URL and prints *body* (empty body: the server is down)."""
    script = bin_dir / "curl"
    script.write_text(
        f'#!/bin/sh\nfor a in "$@"; do last="$a"; done\necho "$last" >> "{log}"\n'
        + (f"cat <<'JSON'\n{body}\nJSON\n" if body else "exit 7\n"),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def _run_probe(tmp_path: Path, env_text: str, body: str) -> tuple[str, str]:
    env_file = tmp_path / "backend.env"
    env_file.write_text(env_text, encoding="utf-8")
    bin_dir, log = tmp_path / "bin", tmp_path / "curl.log"
    bin_dir.mkdir()
    _fake_curl(bin_dir, body, log)
    cmd = _kind_task()["ansible.builtin.shell"]["cmd"].replace(
        "{{ deployed_embedding_env_files | join(' ') }}", str(env_file)
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "MODEL": "phi3:mini",
        "DEFAULT_HOST": "default-host",
        "DEFAULT_PORT": "1",
        "PROBE_TIMEOUT_S": "1",
    }
    out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, check=True, env=env)
    return out.stdout.strip(), log.read_text(encoding="utf-8").strip() if log.exists() else ""


@pytest.mark.parametrize(
    ("body", "verdict"),
    [
        ('{"capabilities": ["embedding"], "details": {}}', "embedding"),
        ('{"details": {},\n "capabilities": ["completion", "tools"]}', "not-embedding"),
        ('{"error": "model not found"}', "unknown"),
        ("", "unknown"),
    ],
)
def test_the_server_answer_maps_to_one_verdict(tmp_path: Path, body: str, verdict: str) -> None:
    assert _run_probe(tmp_path, "AUTOBOT_OLLAMA_HOST=llm-node\nAUTOBOT_OLLAMA_PORT=9\n", body)[0] == verdict


def test_the_probe_asks_the_server_the_env_file_names(tmp_path: Path) -> None:
    _, url = _run_probe(tmp_path, 'export AUTOBOT_OLLAMA_HOST="llm-node"\r\nAUTOBOT_OLLAMA_PORT=9\n', "")
    assert url == "http://llm-node:9/api/show"


@pytest.mark.parametrize("env_text", ["AUTOBOT_OLLAMA_HOST=0.0.0.0\n", "OTHER=1\n"])
def test_a_wildcard_or_missing_host_falls_back_to_the_rendered_default(tmp_path: Path, env_text: str) -> None:
    _, url = _run_probe(tmp_path, env_text, "")
    assert url == "http://default-host:1/api/show"


def test_the_model_reaches_the_shell_through_the_environment_not_the_command() -> None:
    task = _kind_task()
    assert task["environment"]["MODEL"] == "{{ _embedding_model_probe.stdout | trim }}"
    assert "_embedding_model_probe" not in task["ansible.builtin.shell"]["cmd"]


def test_keep_is_refused_only_on_a_definite_not_embedding_verdict() -> None:
    keep = next(t for t in _tasks() if "ansible.builtin.set_fact" in t)
    assert any(_KIND in c and "!=" in c and _NOT in c for c in keep["when"])
    assert any("default('unknown')" in c for c in keep["when"] if _KIND in c)


def test_a_replacement_is_announced_on_every_run_it_happens() -> None:
    tasks = _tasks()
    announce = next(t for t in tasks if "REPLACED" in str(t.get("ansible.builtin.debug", {}).get("msg", "")))
    assert any(_KIND in c and "==" in c and _NOT in c for c in announce["when"])


def test_the_kind_probe_runs_between_the_read_and_the_keep() -> None:
    order = [t.get("register") or next(iter(k for k in t if k.startswith("ansible."))) for t in _tasks()]
    assert order.index("_embedding_model_probe") < order.index(_KIND) < order.index("ansible.builtin.set_fact")
