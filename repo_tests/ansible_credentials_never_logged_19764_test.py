# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""A task that sends or receives credentials never writes them to a log (#19764).

Ansible logs each module invocation's arguments on the target, so a ``uri`` task carrying an
``Authorization`` header, an API key or a password in its body put a live bearer token in a
node's journal (2026-10-27). Every such task sets ``no_log: true``; and no message prints the
whole response of one -- ``no_log`` hides the invocation, not a later ``fail_msg`` that dumps
``<result>.json`` (the login response holds the token).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from repo_tests._paths import repo_root
from repo_tests._reach import declare

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

_ANS = "autobot-slm-backend/ansible"
_HTTP_MODULES = ("ansible.builtin.uri", "uri", "ansible.builtin.get_url", "get_url")
_SECRET = re.compile(r"Authorization|Bearer|X-API-Key|api[_-]?key|password|token|secret", re.IGNORECASE)
_SECRET_ARGS = ("headers", "body", "url_password", "client_key")
_MESSAGE_MODULES = (
    "ansible.builtin.debug",
    "debug",
    "ansible.builtin.fail",
    "fail",
    "ansible.builtin.assert",
    "assert",
)
_CHILD_LISTS = ("block", "rescue", "always", "tasks", "pre_tasks", "post_tasks", "handlers")


def _tasks(node):
    if isinstance(node, list):
        for item in node:
            yield from _tasks(item)
    elif isinstance(node, dict):
        yield node
        for key in _CHILD_LISTS:
            if key in node:
                yield from _tasks(node[key])


def _http_args(task: dict) -> dict | None:
    return next((task[m] for m in _HTTP_MODULES if isinstance(task.get(m), dict)), None)


def _carries_credentials(task: dict) -> bool:
    args = _http_args(task)
    return args is not None and any(_SECRET.search(str(args.get(key, ""))) for key in _SECRET_ARGS)


def _whole_response_dumps(tasks: list[dict], secret_results: set[str]) -> list[str]:
    """Messages that print ``<secret result>.json`` / ``.content`` whole, not one field of it."""
    found = []
    for task in tasks:
        if task.get("no_log"):
            continue
        text = " ".join(str(task.get(m, "")) for m in _MESSAGE_MODULES)
        for name in secret_results:
            if re.search(rf"\b{re.escape(name)}\.(json|content)\s*(\||}}|$)", text):
                found.append(f"{task.get('name', '<unnamed>')} prints {name} whole")
    return found


def findings_in(text: str) -> list[str]:
    """Every credential leak in one YAML document's text."""
    tasks = list(_tasks(yaml.safe_load(text)))
    secret = [t for t in tasks if _carries_credentials(t)]
    found = [f"{t.get('name', '<unnamed>')} sends credentials without no_log" for t in secret if not t.get("no_log")]
    results = {t["register"] for t in secret if isinstance(t.get("register"), str)}
    return found + _whole_response_dumps(tasks, results)


def _ansible_yaml(root: Path) -> list[str]:
    try:
        return [p for p in tracked_paths(root, _ANS) if p.endswith((".yml", ".yaml"))]
    except EmptyEnumeration:
        return []


REACH_FILES = declare(
    "ansible-credentials-never-logged",
    discover=_ansible_yaml,
    # Mid-window, from REACH_FILES.window(); the arithmetic is on the PR, not here.
    floor=317,
    what="ansible yml/yaml files, scanned for credential-bearing http tasks and messages that dump their response",
    roots=(_ANS,),
    growth=50,
)


def test_no_ansible_task_logs_credentials() -> None:
    root = repo_root()
    paths = REACH_FILES.examined(root)
    leaks = []
    for rel in paths:
        try:
            leaks += [f"{rel}: {f}" for f in findings_in((root / rel).read_text(encoding="utf-8"))]
        except yaml.YAMLError:
            continue  # Jinja-only or multi-doc files the other guards own; not a task list
    assert not leaks, "credentials reach a log (#19764):\n  " + "\n  ".join(leaks)


_PLANTED = """
- name: login
  ansible.builtin.uri:
    url: "https://slm/api/auth/login"
    method: POST
    body_format: json
    body: {username: admin, password: "{{ pw }}"}
  register: login_result
- name: report
  ansible.builtin.fail:
    msg: "login failed: {{ login_result.json | default({}) }}"
"""


def test_a_planted_credential_task_without_no_log_is_flagged() -> None:
    assert findings_in(_PLANTED) == [
        "login sends credentials without no_log",
        "report prints login_result whole",
    ]


def test_no_log_and_a_single_field_message_are_accepted() -> None:
    fixed = _PLANTED.replace("  register: login_result", "  register: login_result\n  no_log: true").replace(
        "{{ login_result.json | default({}) }}", "{{ login_result.status }}"
    )
    assert findings_in(fixed) == []


def test_a_header_bearer_token_counts_as_credentials() -> None:
    task = '- name: sync\n  uri: {url: "https://x", headers: {Authorization: "Bearer {{ t }}"}}\n'
    assert findings_in(task) == ["sync sends credentials without no_log"]
