# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""An update never silently changes a deployed embedding model (#15502).

The backend env template defaults AUTOBOT_EMBEDDING_MODEL for FRESH installs only; every render
site first runs the shared read task so a value already on the host wins. A different model means
a different vector space and dimension (see knowledge/index_embedding_space.py, #18185/#18217), so
switching an existing deployment is a re-index proposal, never a template default.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml
from repo_tests._paths import repo_root

_ANS = "autobot-slm-backend/ansible"
_READ = f"{_ANS}/_shared/tasks/read_deployed_embedding_model.yml"
_RENDER_SITES = {
    f"{_ANS}/roles/backend/tasks/env_only.yml": "Backend | Render .env from template (#12871)",
    f"{_ANS}/roles/backend/tasks/main.yml": "Backend | Deploy /etc/autobot/autobot-backend.env",
    f"{_ANS}/roles/backend_services/tasks/main.yml": "Generate environment configuration",
}
_INCLUDE = "read_deployed_embedding_model.yml"


def _probe_script(files: list[str]) -> str:
    task = yaml.safe_load((repo_root() / _READ).read_text(encoding="utf-8"))[0]
    return task["ansible.builtin.shell"]["cmd"].replace(
        "{{ deployed_embedding_env_files | join(' ') }}", " ".join(files)
    )


def _probe(files: list[str]) -> str:
    out = subprocess.run(["bash", "-c", _probe_script(files)], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_a_deployed_value_is_read_back_so_it_is_preserved(tmp_path) -> None:
    env = tmp_path / "backend.env"
    env.write_text("FOO=1\nAUTOBOT_EMBEDDING_MODEL=phi3:mini\n", encoding="utf-8")
    assert _probe([str(env)]) == "phi3:mini"


def test_the_first_file_that_carries_a_value_wins(tmp_path) -> None:
    empty, a, b = tmp_path / "e.env", tmp_path / "a.env", tmp_path / "b.env"
    empty.write_text("AUTOBOT_EMBEDDING_MODEL=\n", encoding="utf-8")
    a.write_text("AUTOBOT_EMBEDDING_MODEL=model-a\n", encoding="utf-8")
    b.write_text("AUTOBOT_EMBEDDING_MODEL=model-b\n", encoding="utf-8")
    assert _probe([str(tmp_path / "missing.env"), str(empty), str(a), str(b)]) == "model-a"


def test_a_fresh_host_reads_nothing_so_the_canonical_default_applies(tmp_path) -> None:
    assert _probe([str(tmp_path / "absent.env")]) == ""


def test_the_adopt_task_only_fires_on_a_non_empty_read() -> None:
    task = yaml.safe_load((repo_root() / _READ).read_text(encoding="utf-8"))[1]
    assert task["ansible.builtin.set_fact"] == {"backend_embedding_model": "{{ _embedding_model_probe.stdout | trim }}"}
    assert "length > 0" in task["when"]


@pytest.mark.parametrize(("rel", "render_task"), sorted(_RENDER_SITES.items()))
def test_every_render_site_reads_the_deployed_value_before_it_renders(rel: str, render_task: str) -> None:
    text = (repo_root() / rel).read_text(encoding="utf-8")
    assert text.count(_INCLUDE) >= 1, f"{rel} renders backend.env.j2 without the preserve step"
    assert text.index(_INCLUDE) < text.index(render_task), rel


def test_the_render_site_check_is_not_vacuous() -> None:
    """Contrast: a file whose include follows the render task is rejected by the ordering rule."""
    bad = '- name: "Render"\n  template: x\n- include_tasks: read_deployed_embedding_model.yml\n'
    assert bad.index(_INCLUDE) > bad.index("Render")
