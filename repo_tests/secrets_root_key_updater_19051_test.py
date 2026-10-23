# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""#19051: the builtin updater must back-fill the root key, and an empty key must stop the deploy.

The #14758 back-fill lived only in the slm_manager role, which update-all-nodes.yml Play 1
never runs. A host kept current by the builtin updater alone never got a key, the backend
rendered `AUTOBOT_SECRETS_ROOT_KEY=`, and every System vault call returned 503.
"""

import re

from repo_tests._paths import repo_root

ANSIBLE = repo_root() / "autobot-slm-backend" / "ansible"
UPDATER = ANSIBLE / "playbooks" / "update-all-nodes.yml"
ROLE_MAIN = ANSIBLE / "roles" / "slm_manager" / "tasks" / "main.yml"
BACKFILL = ANSIBLE / "roles" / "slm_manager" / "tasks" / "secrets_root_key.yml"
BACKEND_TASKS = ANSIBLE / "roles" / "backend" / "tasks" / "main.yml"
_INCLUDE = re.compile(r"name: slm_manager\s+tasks_from: secrets_root_key\.yml")


def _read(path):
    assert path.is_file(), f"{path} is missing — this guard would otherwise pass vacuously"
    return path.read_text(encoding="utf-8")


def test_play_1_runs_the_back_fill_before_the_backend_play() -> None:
    text = _read(UPDATER)
    play1, play2 = text.find('- name: "Play 1 '), text.find('- name: "Play 2 ')
    assert 0 <= play1 < play2, "update-all-nodes.yml no longer has Play 1 before Play 2 — re-point this guard"
    match = _INCLUDE.search(text)
    assert match, "Play 1 must include slm_manager tasks_from: secrets_root_key.yml (#19051)"
    assert play1 < match.start() < play2, "the back-fill must run in Play 1, before the backend env renders"


def test_the_role_and_the_updater_share_one_back_fill() -> None:
    assert "include_tasks: secrets_root_key.yml" in _read(ROLE_MAIN)
    copies = [
        p for p in ANSIBLE.rglob("*.yml") if 'regexp: "^AUTOBOT_SECRETS_ROOT_KEY="' in p.read_text(encoding="utf-8")
    ]
    assert copies == [BACKFILL], f"the back-fill must exist exactly once, in {BACKFILL.name}; found {copies}"


def test_the_back_fill_only_adds() -> None:
    text = _read(BACKFILL)
    add = text[text.index("Add AUTOBOT_SECRETS_ROOT_KEY") :]
    assert "_slm_root_key_check.rc != 0" in add, "the key must be written only when the grep found none"
    assert "create: false" in add


def test_an_empty_key_from_the_slm_file_fails_the_backend_deploy() -> None:
    text = _read(BACKEND_TASKS)
    align = text.index("Align autobot_secrets_root_key with slm-secrets.env")
    guard = text.index("Fail when the SLM secrets file yields no root key")
    render = text.index("src: backend.env.j2")
    assert align < guard < render, "the empty-key failure must sit after the read-back and before the env renders"
    block = text[guard : text.index("\n- name:", guard)]
    assert "ansible.builtin.fail" in block
    assert "_slm_secrets_stat.stat.exists" in block, "it must fire only where the SLM file is the key's source"
    assert "length == 0" in block
