# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A read-only status query in an Ansible task runs in check mode (#19663).

Under `ansible-playbook --check` a `command`/`shell` task is skipped and registers no
`stdout`, so a later `when:` that reads the result fails with an undefined attribute and
the dry run dies at the probe instead of reporting what it would change. A query reads
state and changes nothing, so it is safe in check mode: it carries `check_mode: false`
(run it anyway) and `changed_when: false` (it never reports a change).

The queries are `dpkg-query`, `apt-cache policy`, `systemctl is-active|is-enabled`,
`<binary> --version` and `rpm -q`. Only a command that STARTS with one is a query: a
mutating task is never given `check_mode: false` (`apt`, `systemctl start`, or a command
that chains a second command after the query).

Detection lives in `repo_tests/_ansible_query_tasks.py`. Deliberately not matched, and
left alone: compound shell (`if ...; then ... --version ...`) and `<query> && <mutation>`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from repo_tests._ansible_query_tasks import has_check_mode_off, is_status_query, query_tasks
from repo_tests._paths import repo_root
from repo_tests._reach import declare

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

_TREE = "autobot-slm-backend/ansible"


def _ansible_yaml(root: Path) -> list[Path]:
    """Tracked YAML under the SLM ansible tree; ``[]`` on an empty tree (never raises)."""
    try:
        tracked = tracked_paths(root, _TREE)
    except EmptyEnumeration:
        return []
    return [root / rel for rel in tracked if rel.endswith((".yml", ".yaml"))]


#: 346 tracked YAML files measured at declaration; ``growth`` because roles add task files steadily.
REACH = declare(
    "ansible-status-queries-check-mode-sweep",
    discover=_ansible_yaml,
    floor=330,
    growth=40,
    skips=0,
    what="Ansible YAML files",
    roots=(_TREE,),
)

_QUERY_COMMANDS = (
    "dpkg-query -W --showformat=${Status} nginx",
    "apt-cache policy grafana",
    "systemctl is-active nginx",
    "systemctl is-enabled nginx",
    "node --version",
    "{{ venv }}/bin/python --version",
    "rpm -q nginx",
)


def _task(command: str, module: str = "ansible.builtin.command", **extra: object) -> dict:
    return {"name": "probe", module: {"cmd": command}, **extra}


def test_every_status_query_runs_in_check_mode() -> None:
    """Each query task carries `check_mode: false` and `changed_when: false`."""
    scanned = REACH.examined(repo_root())
    missing: list[str] = []
    count = 0
    for path in scanned:
        for task in query_tasks(path.read_text(encoding="utf-8")):
            count += 1
            if not has_check_mode_off(task):
                missing.append(f"{path.relative_to(repo_root())}: {task.get('name')}")
    assert count, "the detector found no status query at all -- it stopped matching"
    assert not missing, (
        "these read-only status queries are skipped under --check, so a dry run dies at the task that reads "
        "them; add `check_mode: false` and `changed_when: false` (#19663):\n  " + "\n  ".join(missing)
    )


@pytest.mark.parametrize("command", _QUERY_COMMANDS)
@pytest.mark.parametrize("module", ["command", "shell", "ansible.builtin.command", "ansible.builtin.shell"])
def test_a_query_without_check_mode_is_flagged(command: str, module: str) -> None:
    """Contrast: a query lacking `check_mode: false` is a query and is not accepted."""
    task = _task(command, module, changed_when=False)
    assert is_status_query(task)
    assert not has_check_mode_off(task)


@pytest.mark.parametrize("command", _QUERY_COMMANDS)
def test_a_query_with_check_mode_off_is_accepted(command: str) -> None:
    """Contrast: the fixed spelling passes."""
    assert has_check_mode_off(_task(command, check_mode=False, changed_when=False))


def test_check_mode_without_changed_when_is_flagged() -> None:
    """Both keys are required: `changed_when: false` is what keeps the query out of the change count."""
    assert not has_check_mode_off(_task("node --version", check_mode=False))


@pytest.mark.parametrize(
    "task",
    [
        {"name": "install", "ansible.builtin.apt": {"name": "nginx", "state": "present"}},
        {"name": "start", "ansible.builtin.systemd": {"name": "nginx", "state": "started"}},
        _task("systemctl start nginx"),
        _task("systemctl restart nginx"),
        _task("apt-get install -y nginx"),
        _task("systemctl is-active nginx && systemctl reload nginx"),
        _task("dpkg-query -W nginx; apt-get install -y nginx"),
        _task("dpkg-query -W nginx\napt-get install -y nginx"),
    ],
    ids=[
        "apt",
        "systemd-start",
        "systemctl-start",
        "systemctl-restart",
        "apt-get",
        "chained-reload",
        "chained-install",
        "newline-chained",
    ],
)
def test_a_mutating_task_is_not_a_query(task: dict) -> None:
    """Contrast: a mutating task is ignored, so it is never handed `check_mode: false`."""
    assert not is_status_query(task)


def test_queries_are_found_in_nested_blocks() -> None:
    """A query inside `block:` is found, so grouping tasks does not hide one."""
    text = "- hosts: all\n  tasks:\n    - block:\n        - name: q\n          command: node --version\n"
    assert [t["name"] for t in query_tasks(text)] == ["q"]
