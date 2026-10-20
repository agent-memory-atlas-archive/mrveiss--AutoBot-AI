# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The ONE matcher for "a module builds its own Redis client" (#18553, parent #17312, #1086).

Redis access goes through `autobot_shared.redis_client` (`get_redis_client` /
`get_async_redis_client`), which owns pooling, auth, TLS, the circuit breaker and the named
databases. This module is the single definition of what counts as a direct construction, where it
is looked for, and how a count is judged against the frozen baseline
(`repo_tests/redis_direct_constructor_baseline_18553.py`). Three callers share it and none keeps a
copy: the whole-tree ratchet (`repo_tests/redis_direct_constructor_18553_test.py`), the
`no-direct-redis` pre-commit hook, and the code-quality CI step (both run `main()` on the changed
files). The older regex hook is retired, and with it `# noqa: redis`: a site is in the baseline with a
verdict or it fails.

WHAT IS A CONSTRUCTION
    A call whose callee resolves through the file's own `import redis...` / `from redis...
    import ...` bindings to `redis.*` (or the legacy `aioredis.*`) and ends in `Redis`,
    `StrictRedis`, `ConnectionPool`, `BlockingConnectionPool`, `Sentinel`, `RedisCluster`,
    `from_url` or `from_pool`. Resolving the import is what keeps SQLite / Ollama `ConnectionPool`
    classes out. Python is read from the syntax tree, so a comment, docstring or string naming
    `redis.Redis(` is not a site. Shell scripts that embed Python are read line by line with comments
    removed (`tools.lint._comment_syntax`).

BOUNDARY
    * Scanned: `.py` and `.sh` under `SCAN_ROOTS`, minus tests (`*_test.py`, `tests/`,
      `conftest.py`). The manual `analysis/test_*.py` scripts are NOT tests and are scanned.
    * Exempt by design: `autobot_shared/redis*` -- the canonical engine itself.
    * NOT seen: a client built by a library from a URI (LangGraph's checkpointer, Celery broker URLs),
      `getattr`/`__import__` indirection, and Python embedded in file types other than `.sh`.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from tools.lint._comment_syntax import code_lines

SCAN_ROOTS = (
    "autobot-backend",
    "autobot-slm-backend",
    "autobot_shared",
    "autobot-infrastructure",
    "autobot-npu-worker",
    "autobot-tts-worker",
    "autobot-browser-worker",
    "scripts",
    "pipeline-scripts",
    "tools",
    "plugins",
    "libs",
    "debug",
)
EXEMPT_PREFIX = "autobot_shared/redis"
CLIENT_ROOTS = frozenset({"redis", "aioredis"})
CLIENT_NAMES = frozenset(
    {
        "Redis",
        "StrictRedis",
        "ConnectionPool",
        "BlockingConnectionPool",
        "Sentinel",
        "RedisCluster",
        "from_url",
        "from_pool",
    }
)
#: Shell scripts embed Python as text; this is the same predicate over code lines.
SHELL_SITE = re.compile(
    r"\b(?:aioredis|redis(?:\.(?:asyncio|sentinel|cluster))?)\."
    r"(?:Redis|StrictRedis|ConnectionPool|BlockingConnectionPool|Sentinel|RedisCluster|from_url|from_pool)\(|"
    r"\bRedis\.(?:from_url|from_pool)\("
)


def is_scanned(rel: str) -> bool:
    """True when the guard reads *rel*: source, not a test, not the canonical engine."""
    name = rel.rsplit("/", 1)[-1]
    if not rel.endswith((".py", ".sh")) or rel.startswith(EXEMPT_PREFIX):
        return False
    return not (name.endswith("_test.py") or name == "conftest.py" or "/tests/" in rel or "/test/" in rel)


def in_scope(rel: str) -> bool:
    """`is_scanned` AND under one of `SCAN_ROOTS` (the tree guard's population)."""
    return is_scanned(rel) and rel.split("/", 1)[0] in SCAN_ROOTS


def _bindings(tree: ast.AST) -> dict[str, str]:
    """Local name -> dotted path for every `redis` import anywhere in the file (nested too)."""
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in CLIENT_ROOTS:
                    bound[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] in CLIENT_ROOTS:
            for a in node.names:
                bound[a.asname or a.name] = f"{node.module}.{a.name}"
    return bound


def _dotted(func: ast.AST) -> list[str] | None:
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if not isinstance(func, ast.Name):
        return None
    parts.append(func.id)
    return parts[::-1]


def python_sites(source: str) -> list[int]:
    """Line numbers of direct Redis client constructions in `source`."""
    tree = ast.parse(source)
    bound = _bindings(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        parts = _dotted(node.func) if isinstance(node, ast.Call) else None
        if not parts or parts[0] not in bound:
            continue
        full = bound[parts[0]].split(".") + parts[1:]
        if full[0] in CLIENT_ROOTS and full[-1] in CLIENT_NAMES:
            lines.append(node.lineno)
    return lines


def shell_sites(source: str, name: str) -> list[int]:
    return [cl.lineno for cl in code_lines(source, name=name) if SHELL_SITE.search(cl.text)]


def sites_in(rel: str, source: str) -> list[int]:
    return python_sites(source) if rel.endswith(".py") else shell_sites(source, rel)


def verdict_problems(
    live: dict[str, int], baseline: dict[str, tuple[int, str, str]], scope: frozenset[str] | None = None
) -> list[str]:
    """NEW / GREW / STALE findings of *live* against *baseline*, optionally limited to *scope* paths."""
    out: list[str] = []
    for rel, n in sorted(live.items()):
        if rel not in baseline:
            out.append(f"NEW {rel}: {n} direct Redis construction(s)")
        elif n > baseline[rel][0]:
            out.append(f"GREW {rel}: {n} sites, baseline allows {baseline[rel][0]}")
    for rel, (count, _verdict, _why) in sorted(baseline.items()):
        if (scope is None or rel in scope) and live.get(rel, 0) < count:
            out.append(f"STALE {rel}: baseline {count}, tree {live.get(rel, 0)} -- lower or remove the entry")
    return out


def count_sites(root: Path, rels: list[str]) -> dict[str, int]:
    """Per-file site count over *rels* (repo-relative), skipping files that cannot hold one."""
    found: dict[str, int] = {}
    for rel in rels:
        source = (root / rel).read_text(encoding="utf-8")
        if "edis" not in source:
            continue
        n = len(sites_in(rel, source))
        if n:
            found[rel] = n
    return found


def _report(problems: list[str]) -> int:
    for line in problems:
        print(f"  {line}")  # noqa: print
    if problems:
        print(
            "#18553: use `autobot_shared.redis_client.get_redis_client` / `get_async_redis_client` "
            "(named database, pooled, authenticated); a site that cannot is frozen, with a reason, in "
            "repo_tests/redis_direct_constructor_baseline_18553.py (it may only shrink)."
        )
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    """Judge the given repo-relative files against the baseline; 1 on a NEW/GREW/STALE finding."""
    from repo_tests.redis_direct_constructor_baseline_18553 import BASELINE

    root = Path.cwd()
    rels = sorted({a for a in argv if in_scope(a) and (root / a).is_file()})
    return _report(verdict_problems(count_sites(root, rels), BASELINE, frozenset(rels)))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
