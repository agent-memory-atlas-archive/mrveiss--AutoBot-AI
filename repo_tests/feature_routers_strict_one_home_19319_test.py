# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""`AUTOBOT_FEATURE_ROUTERS_STRICT` has one home: two SSOT profile constants (#19319).

#6690 ruled strict boot for dev/CI and a degraded start for the managed fleet. The fleet value
lived only as a literal ``0`` in ``backend.env.j2`` while the SSOT field said ``"1"``, so the
default a reader saw depended on which file it opened. Now:

* ``FEATURE_ROUTERS_STRICT_DEFAULT`` / ``FEATURE_ROUTERS_STRICT_FLEET`` are assigned once each, in
  ``ssot_role_mirrors``, and the ``ssot_config`` ``MiscConfig`` field defaults to the first by name;
* the backend role default ``backend_feature_routers_strict`` mirrors the fleet constant (Ansible
  cannot import Python) and the template renders that variable, never a literal;
* no other tracked file sets the variable to a literal: an env/compose/YAML assignment, a Python
  read with a literal fallback, or a second role/group default.

A planted copy of each shape proves the sweep can fail, so its green is not a blind detector's.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml
from repo_tests._paths import repo_root
from repo_tests._reach import declare

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

_ROOT = repo_root()
_NAME = "AUTOBOT_FEATURE_ROUTERS_STRICT"
_ROLE_VAR = "backend_feature_routers_strict"
_SSOT = "autobot_shared/ssot_config.py"
_MIRRORS = "autobot_shared/ssot_role_mirrors.py"
_ROLE_DEFAULTS = "autobot-slm-backend/ansible/roles/backend/defaults/main.yml"
_TEMPLATE = "autobot-slm-backend/ansible/roles/backend/templates/backend.env.j2"
_TEMPLATE_LINE = f"{_NAME}={{{{ {_ROLE_VAR} }}}}"
_PROFILES = ("FEATURE_ROUTERS_STRICT_DEFAULT", "FEATURE_ROUTERS_STRICT_FLEET")
_SKIP_PARTS = frozenset({"__pycache__", "node_modules", ".worktrees", "venv", ".venv"})
#: ``NAME=0`` / ``- NAME=0`` / ``export NAME=0`` / ``NAME: "0"`` / ``backend_feature_routers_strict: "1"``
#: -- an assignment to a literal. A Jinja ``{{ var }}`` value is a derivation, not a copy.
_LITERAL_ASSIGNMENT = re.compile(
    rf"""^\s*(?:-\s*)?(?:export\s+)?["']?(?:{_NAME}|{_ROLE_VAR})["']?\s*[:=]\s*(?!\{{\{{)["']?[^\s"'{{#]""",
    re.MULTILINE,
)
#: A fixture deliberately setting a value is configuration of that test, not a default.
_SETTERS = frozenset({"setenv", "setitem"})


def _candidates(root: Path) -> list[str]:
    try:
        paths = tracked_paths(root)
    except EmptyEnumeration:
        return []
    return [p for p in paths if not _SKIP_PARTS & set(Path(p).parts)]


REACH = declare(
    "feature-routers-strict-one-home-19319",
    discover=_candidates,
    floor=12151,  # mid-window: population 12351 tracked files on 9b5a8088e3, window [11951, 12351]
    growth=400,
    what="all tracked files, repo-wide",
)


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _is_literal_default_call(call: ast.Call) -> bool:
    """``getenv(NAME, "1")`` / ``Field(default="1", alias=NAME)`` -- NAME plus a literal value."""
    if _call_name(call) in _SETTERS:
        return False
    values = [*call.args, *(k.value for k in call.keywords if k.arg in {"alias", "default", None})]
    strings = [v.value for v in values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    return _NAME in strings and any(s != _NAME for s in strings)


def _python_copies(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return bool(_LITERAL_ASSIGNMENT.search(text))
    return any(isinstance(n, ast.Call) and _is_literal_default_call(n) for n in ast.walk(tree))


def literal_copies(files: dict[str, str]) -> list[str]:
    """Every file (outside the role default itself) that gives the flag a literal value."""
    found = []
    for rel, text in files.items():
        if rel == _ROLE_DEFAULTS or (_NAME not in text and _ROLE_VAR not in text):
            continue
        hit = _python_copies(text) if rel.endswith(".py") else bool(_LITERAL_ASSIGNMENT.search(text))
        if hit:
            found.append(rel)
    return sorted(found)


def _live_files() -> dict[str, str]:
    files = {}
    for rel in REACH.examined(_ROOT):
        try:
            files[rel] = (_ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary asset; holds no env assignment
    return files


def _tree(rel: str) -> ast.Module:
    return ast.parse((_ROOT / rel).read_text(encoding="utf-8"))


def _profile_values() -> dict[str, list[str]]:
    values: dict[str, list[str]] = {name: [] for name in _PROFILES}
    for node in _tree(_MIRRORS).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in values:
                    values[target.id].append(node.value.value)
    return values


def _field_default() -> ast.expr:
    for node in ast.walk(_tree(_SSOT)):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "feature_routers_strict":
            assert isinstance(node.value, ast.Call), "feature_routers_strict is no longer a Field(...)"
            return next(k.value for k in node.value.keywords if k.arg == "default")
    raise AssertionError(f"{_SSOT} no longer declares feature_routers_strict")


def test_each_profile_is_assigned_once_and_the_two_differ() -> None:
    values = _profile_values()
    assert all(len(v) == 1 for v in values.values()), f"each profile constant must be assigned once: {values}"
    assert values["FEATURE_ROUTERS_STRICT_DEFAULT"] == ["1"], "the unset default must stay strict (#6690)"
    assert values["FEATURE_ROUTERS_STRICT_FLEET"] == ["0"], "the fleet profile is the #6690 degraded start"


def test_the_field_defaults_to_the_default_profile_by_name() -> None:
    default = _field_default()
    assert isinstance(default, ast.Name) and default.id == "FEATURE_ROUTERS_STRICT_DEFAULT", ast.dump(default)
    imported = {
        alias.name
        for node in _tree(_SSOT).body
        if isinstance(node, ast.ImportFrom) and node.module == "autobot_shared.ssot_role_mirrors"
        for alias in node.names
    }
    assert default.id in imported, f"{_SSOT} must take the default profile from {_MIRRORS}, not define its own"


def test_the_role_default_mirrors_the_fleet_profile() -> None:
    role = yaml.safe_load((_ROOT / _ROLE_DEFAULTS).read_text(encoding="utf-8"))
    assert [str(role[_ROLE_VAR])] == _profile_values()["FEATURE_ROUTERS_STRICT_FLEET"]


def test_the_template_renders_the_role_variable_once() -> None:
    lines = [ln for ln in (_ROOT / _TEMPLATE).read_text(encoding="utf-8").splitlines() if ln.startswith(_NAME)]
    assert lines == [_TEMPLATE_LINE], lines


def test_no_tracked_file_holds_a_literal_copy() -> None:
    files = _live_files()
    assert _TEMPLATE in files and _SSOT in files, "the sweep did not reach the two files it must see"
    assert literal_copies(files) == [], f"a second default for {_NAME}; derive from ssot_role_mirrors instead"


def test_every_planted_copy_is_caught_and_derivations_are_not() -> None:
    copies = {
        ".env.example": f"{_NAME}=0\n",
        "docker-compose.yml": f"    environment:\n      - {_NAME}=1\n",
        "values.yaml": f'{_NAME}: "0"\n',
        "deploy.sh": f"export {_NAME}=1\n",
        "inventory/group_vars/backend.yml": f'{_ROLE_VAR}: "1"\n',
        "reader.py": f'import os\nSTRICT = os.getenv("{_NAME}", "1")\n',
        "settings.py": f'x: str = Field(default="0", alias="{_NAME}")\n',
    }
    derived = {
        _TEMPLATE: f"# prose naming {_NAME}=0 inline\n{_TEMPLATE_LINE}\n",
        "test_x.py": f'def t(monkeypatch):\n    monkeypatch.setenv("{_NAME}", "1")\n',
        "feature.py": f'"""Strict mode via ``{_NAME}=1``."""\nmsg = f"{_NAME}=1 failed"\n',
        _SSOT: f'f: str = Field(default=FEATURE_ROUTERS_STRICT_DEFAULT, alias="{_NAME}", description="d")\n',
    }
    assert literal_copies({**copies, **derived}) == sorted(copies)
