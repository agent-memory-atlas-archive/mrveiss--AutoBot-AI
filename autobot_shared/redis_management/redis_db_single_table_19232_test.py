# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Guard: ONE name -> Redis database number table in production Python (#19232).

``redis-databases.yaml`` (+ ``AUTOBOT_REDIS_DB_*`` overrides) is the table, mirrored only
by ``types._FALLBACK_MAPPING`` for a checkout without the YAML; the mirror is pinned to the
YAML by ``autobot-infrastructure/shared/tests/test_redis_db_ssot.py``. Every other reader
goes through ``database_names.database_number``.

A second table looks like one of the shapes #19232 removed:

* a dict literal mapping two or more database names (or their ``AUTOBOT_REDIS_DB_*``
  variables) to number literals -- ``cache_management.REDIS_DATABASES`` had
  ``sessions: 4`` and ``analytics: 9`` while the client used 6 and 11;
* a class body assigning number literals to two or more database names -- an enum copy;
* a settings field whose alias is an ``AUTOBOT_REDIS_DB_*`` variable and whose default is a
  number -- ``ssot_config.RedisConfig.db_*``, which moved the backend's readers but not the
  canonical client;
* a second redis-databases.yaml path list -- ``types`` and ``RedisConfigLoader`` each had
  one, with different candidates, so the table and the per-database tuning could read
  different files. ``types.resolve_databases_yaml`` is the one resolver;
* a second Redis URL builder -- a ``redis://``/``rediss://`` literal or f-string outside
  ``endpoint.py`` (``NetworkConstants`` ``REDIS_VM``/``REDIS_LOCAL``, a PKI ``rediss://..:6380``);
* a shell/compose/env/Ansible copy -- ``${AUTOBOT_REDIS_DB_CELERY_BROKER:-14}``,
  ``AUTOBOT_REDIS_DB_LOGS=5`` in an installer, or a URL with a literal ``/<db>``.

Each shape is shown FAILING against a deliberately re-added copy before the tree is swept,
so a green sweep is not a detector that matches nothing.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

from autobot_shared.redis_management.database_overrides import LEGACY_ENV_NAMES, env_var_name
from autobot_shared.redis_management.types import _ALIASES, _FALLBACK_MAPPING

_REPO = Path(__file__).resolve().parents[2]
_ROOTS = ("autobot_shared", "autobot-backend", "autobot-slm-backend", "autobot-slm-agent", "autobot-infrastructure")
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "tests", "test"}
#: (repo-relative path, assigned name) of the one allowed literal mirror.
_ALLOWED = {("autobot_shared/redis_management/types.py", "_FALLBACK_MAPPING")}
#: The one file allowed to spell the YAML's file name as a path.
_YAML_RESOLVER = "autobot_shared/redis_management/types.py"
_YAML_NAME = "redis-databases.yaml"

#: The one URL builder, and a validator that checks an env FILE's URL against that file's own
#: host/port (it has no process environment to resolve from).
_URL_BUILDERS = {
    "autobot_shared/redis_management/endpoint.py",
    "autobot-infrastructure/shared/scripts/validate-env-files.py",
}
_CONFIG_SUFFIXES = (".sh", ".yml", ".yaml", ".j2", ".service", ".conf")
#: A DB number pinned in config: `${X:-14}`, `: "${X:=14}"`, `X=14`, `X: 14`.
_CONFIG_DB_LITERAL = re.compile(r"AUTOBOT_REDIS_DB_[A-Z_]+\s*(?::?[-=]|:)\s*['\"]?\d")
#: A Redis URL naming its database by number.
_URL_DB_LITERAL = re.compile(r"rediss?://[^\s\"'`]*/\d+\b")

_NAMES = set(_FALLBACK_MAPPING) | set(_ALIASES)
_KEYS = _NAMES | {env_var_name(n) for n in _NAMES} | set(LEGACY_ENV_NAMES)


def _is_number(node: ast.AST | None) -> bool:
    """An int literal, or a string literal of digits (env-file style)."""
    if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
        return False
    return isinstance(node.value, int) or (isinstance(node.value, str) and node.value.isdigit())


def _dict_hits(node: ast.Dict) -> int:
    """How many entries of *node* map a database key to a number literal."""
    return sum(
        1
        for key, value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant) and key.value in _KEYS and _is_number(value)
    )


def _class_hits(node: ast.ClassDef) -> int:
    """How many class-body assignments give a database name a number literal."""
    return sum(
        1
        for stmt in node.body
        if isinstance(stmt, ast.Assign)
        and _is_number(stmt.value)
        and any(isinstance(t, ast.Name) and t.id.lower().removesuffix("_db") in _NAMES for t in stmt.targets)
    )


def _is_db_field(node: ast.Call) -> bool:
    """``Field(default=<number>, alias="AUTOBOT_REDIS_DB_...")``."""
    kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
    alias = kwargs.get("alias")
    is_db_alias = isinstance(alias, ast.Constant) and str(alias.value).startswith("AUTOBOT_REDIS_DB_")
    return is_db_alias and _is_number(kwargs.get("default"))


def _is_yaml_path(node: ast.AST) -> bool:
    """A string literal naming redis-databases.yaml as a file or path (not prose mentioning it)."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.split("/")[-1] == _YAML_NAME


def _url_prefix(node: ast.AST) -> str | None:
    """The leading text of a string literal or f-string, else None."""
    if isinstance(node, ast.JoinedStr) and node.values:
        node = node.values[0]
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_url_builder(node: ast.AST) -> bool:
    """A string or f-string that composes a Redis URL (more than the bare scheme)."""
    prefix = _url_prefix(node)
    if prefix is None or not prefix.startswith(("redis://", "rediss://")):
        return False
    # f"redis://{host}..." composes one; a bare "redis://" constant is a scheme (prefix checks)
    return isinstance(node, ast.JoinedStr) or prefix not in ("redis://", "rediss://")


def _assigned_names(tree: ast.AST) -> dict[int, str]:
    """``{id(dict node): target name}`` for dicts assigned straight to a name."""
    names: dict[int, str] = {}
    for node in ast.walk(tree):
        target = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(target, ast.Name):
            names[id(node.value)] = target.id
    return names


def find_tables(source: str, path: str) -> list[str]:
    """``path:line kind`` for every name -> number table in *source* the allowlist does not cover."""
    tree = ast.parse(source)
    assigned = _assigned_names(tree)
    in_fstring = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and _dict_hits(node) >= 2:
            if (path, assigned.get(id(node), "")) not in _ALLOWED:
                found.append(f"{path}:{node.lineno} dict {assigned.get(id(node), '<expr>')}")
        elif isinstance(node, ast.ClassDef) and _class_hits(node) >= 2:
            found.append(f"{path}:{node.lineno} class {node.name}")
        elif isinstance(node, ast.Call) and _is_db_field(node):
            found.append(f"{path}:{node.lineno} AUTOBOT_REDIS_DB_* field")
        elif _is_yaml_path(node) and path != _YAML_RESOLVER:
            found.append(f"{path}:{node.lineno} yaml path")
        elif _is_url_builder(node) and id(node) not in in_fstring and path not in _URL_BUILDERS:
            found.append(f"{path}:{node.lineno} redis url builder")
    return found


def find_config_literals(text: str, path: str) -> list[str]:
    """``path:line`` for every non-comment config line pinning a Redis DB number."""
    found: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if _CONFIG_DB_LITERAL.search(line) or _URL_DB_LITERAL.search(line):
            found.append(f"{path}:{lineno} config literal")
    return found


def _production_sources() -> list[Path]:
    """Every non-test ``.py`` under the production roots."""
    paths: list[Path] = []
    for root in _ROOTS:
        for dirpath, dirnames, filenames in os.walk(_REPO / root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            paths += [
                Path(dirpath) / f
                for f in filenames
                if f.endswith(".py") and not f.endswith("_test.py") and not f.startswith("test_") and f != "conftest.py"
            ]
    return paths


def _config_sources() -> list[Path]:
    """Shell, compose, Ansible and env files under the production roots and the repo root."""
    paths = [p for p in _REPO.iterdir() if p.is_file() and (p.name.startswith(".env") or p.suffix in _CONFIG_SUFFIXES)]
    for root in _ROOTS:
        for dirpath, dirnames, filenames in os.walk(_REPO / root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            paths += [Path(dirpath) / f for f in filenames if f.endswith(_CONFIG_SUFFIXES) or f.startswith(".env")]
    return paths


# --- the detector, shown failing on deliberately re-added copies -------------------------

_OLD_SSOT_FIELDS = """
class RedisConfig(BaseSettings):
    db_main: int = Field(default=0, alias="AUTOBOT_REDIS_DB_MAIN")
    db_knowledge: int = Field(default=1, alias="AUTOBOT_REDIS_DB_KNOWLEDGE")
"""

_OLD_CACHE_TABLE = """
REDIS_DATABASES = {"main": 0, "knowledge": 1, "sessions": 4, "analytics": 9, "websockets": 10}
"""

_ENUM_COPY = """
class Databases(Enum):
    MAIN = 0
    KNOWLEDGE = 1
"""

_CONSTANTS_COPY = """
class DatabaseConstants:
    MAIN_DB = 0
    KNOWLEDGE_DB = 1
"""

_SECOND_YAML_RESOLVER = """
possible_paths = ["/app/config/redis-databases.yaml", os.path.join(base, "config/redis-databases.yaml")]
"""

_ENV_TABLE_COPY = """
expected_dbs = {"AUTOBOT_REDIS_DB_MAIN": "0", "AUTOBOT_REDIS_DB_TESTING": "15"}
"""


def test_detector_flags_the_retired_ssot_fields():
    assert len(find_tables(_OLD_SSOT_FIELDS, "x.py")) == 2


def test_detector_flags_the_retired_cache_management_table():
    assert find_tables(_OLD_CACHE_TABLE, "x.py") == ["x.py:2 dict REDIS_DATABASES"]


def test_detector_flags_an_enum_with_literal_numbers():
    assert find_tables(_ENUM_COPY, "x.py") == ["x.py:2 class Databases"]


def test_detector_flags_db_suffixed_constants():
    assert find_tables(_CONSTANTS_COPY, "x.py") == ["x.py:2 class DatabaseConstants"]


def test_detector_flags_a_second_yaml_path_list():
    """The RedisConfigLoader candidate list #19232 collapsed into types.resolve_databases_yaml."""
    assert len(find_tables(_SECOND_YAML_RESOLVER, "x.py")) == 2
    assert find_tables(_SECOND_YAML_RESOLVER, _YAML_RESOLVER) == []


def test_detector_flags_an_env_variable_table():
    assert find_tables(_ENV_TABLE_COPY, "x.py") == ["x.py:2 dict expected_dbs"]


def test_detector_flags_a_second_copy_of_the_fallback_inside_types_py():
    """The allowlist names ONE assignment: the same literal under another name still fails."""
    types_path = "autobot_shared/redis_management/types.py"
    source = (_REPO / types_path).read_text(encoding="utf-8")
    assert find_tables(source, types_path) == [], "the shipped types.py must pass"
    copied = source + "\n_SECOND_TABLE = " + repr(dict(_FALLBACK_MAPPING)) + "\n"
    assert len(find_tables(copied, types_path)) == 1


_SECOND_URL_BUILDERS = """
REDIS_VM = lambda: f"redis://{NetworkConstants.REDIS_VM_IP}:{NetworkConstants.REDIS_PORT}"
TLS = "rediss://10.0.0.1:6380"
PREFIXES = ("redis://", "postgresql://")
"""

_OLD_SHELL_AND_COMPOSE = """
: "${AUTOBOT_REDIS_DB_CELERY_BROKER:=14}"
CELERY_BROKER_DB="${AUTOBOT_REDIS_DB_CELERY_BROKER:-14}"
      - CELERY_BROKER_URL=redis://autobot-redis:6379/${AUTOBOT_REDIS_DB_CELERY_BROKER:-14}
AUTOBOT_REDIS_DB_LOGS=5
    AUTOBOT_REDIS_DB_TESTING: 15
# AUTOBOT_REDIS_DB_MAIN=0 is a comment, and comments are prose
AUTOBOT_REDIS_DB_MAIN="$(autobot_redis_db main)"
"""


def test_detector_flags_a_second_url_builder():
    """The f-string and the literal; the bare scheme in a prefix tuple is not a URL."""
    assert sorted(find_tables(_SECOND_URL_BUILDERS, "x.py")) == ["x.py:2 redis url builder", "x.py:3 redis url builder"]
    assert find_tables(_SECOND_URL_BUILDERS, "autobot_shared/redis_management/endpoint.py") == []


def test_detector_flags_shell_compose_env_and_ansible_copies():
    assert [hit.split(" ")[0] for hit in find_config_literals(_OLD_SHELL_AND_COMPOSE, "x")] == [
        "x:2",
        "x:3",
        "x:4",
        "x:5",
        "x:6",
    ]


def test_detector_ignores_a_single_value():
    assert find_tables('DB = {"main": 0}\n', "x.py") == []


# --- the sweep ------------------------------------------------------------------------


def test_production_python_has_one_name_to_number_table():
    sources = _production_sources()
    assert len(sources) > 1000, f"sweep reached only {len(sources)} files -- the walk is broken, not the tree clean"
    found = [
        hit
        for path in sources
        for hit in find_tables(path.read_text(encoding="utf-8"), path.relative_to(_REPO).as_posix())
    ]
    assert found == [], "second name -> Redis DB table(s); resolve through database_number():\n" + "\n".join(found)


def test_config_files_pin_no_redis_database_number():
    sources = _config_sources()
    assert len(sources) > 100, f"sweep reached only {len(sources)} config files -- the walk is broken"
    found = [
        hit
        for path in sources
        for hit in find_config_literals(
            path.read_text(encoding="utf-8", errors="replace"), path.relative_to(_REPO).as_posix()
        )
    ]
    assert found == [], "Redis DB number pinned outside redis-databases.yaml (#19232):\n" + "\n".join(found)
