# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A value with an SSOT definition is not written out again under the same name (#19730).

The scan groups every assignment, and every pydantic ``Field(default=...)``, by
(normalised name, literal value) across the Python trees. A group with a member in an
SSOT file and a member anywhere else is a re-hardcode: changing the SSOT value would
leave the copy behind. Each copy imports the SSOT symbol instead, or is renamed when it
is a different concept that only shares the name.

``PENDING`` holds the copies not fixed yet, each with the reason it waits. It only
shrinks. ``DISTINCT`` holds public names (API fields, enum members) that are a
different concept, where a rename would change a wire contract.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict

from repo_tests._paths import repo_root

from tools.lint._scan_helpers import tracked_paths

_ROOT = repo_root()
_ROOTS = ("autobot-backend", "autobot_shared", "autobot-slm-backend")
SSOT = frozenset({"autobot_shared/ssot_config.py", "autobot_shared/ssot_constants.py"})
_TEST_PATH = re.compile(r"(^|/)(tests?|testing|fixtures)/|_test\.py$|(^|/)test_[^/]*\.py$")
#: 3321 tracked non-test Python files under the three roots at #19730.
MIN_FILES = 2800

_BUSY = "fixed once the open PR that also edits this file lands"
_CEILING = "the file is at its size ceiling with no line to absorb the import; fixed with its split"
PENDING: dict[str, str] = {
    "autobot-backend/api/schemas_knowledge.py::page_size=50": f"QueryDefaults; {_BUSY} (#19711)",
    "autobot-backend/api/schemas_knowledge.py::quality_threshold=0.7": f"AgentThresholds; {_BUSY} (#19711)",
    "autobot-backend/api/schemas_knowledge.py::top_k=10": f"QueryDefaults; {_BUSY} (#19711)",
    "autobot-backend/code_analysis/src/ownership_analyzer.py::TTL_1_HOUR=3600": f"{_BUSY} (#19696)",
    "autobot-backend/llc/models/enums.py::CLOSED='closed'": f"classified with the fix; {_BUSY} (#19598)",
    "autobot-backend/llc/models/enums.py::PLAN='plan'": f"classified with the fix; {_BUSY} (#19598)",
    "autobot_shared/redis_management/config.py::_BACKOFF_BASE=2.0": f"RetryConfig; {_BUSY} (#19365, #19400, #19722)",
    "autobot_shared/redis_management/config.py::_CIRCUIT_BREAKER_THRESHOLD=5": f"{_BUSY} (#19365, #19400, #19722)",
    "autobot_shared/redis_management/connection_manager.py::_BACKOFF_BASE=2.0": f"RetryConfig; {_BUSY} (#19365)",
    "autobot_shared/ssot_tls.py::ssh_user='autobot'": f"config.path.ssh_user; {_BUSY} (#19400, #19722)",
    "autobot-backend/api/http_client_mcp.py::DEFAULT_TIMEOUT=30": f"config.timeout.mcp_call; {_CEILING}",
    "autobot-backend/api/schemas_analytics.py::provider='ollama'": f"config.llm.llamaindex_embedding_provider; {_CEILING}",
    "autobot-backend/api/schemas_system.py::admin_username='admin'": f"config.auth.admin_username; {_CEILING}",
}

DISTINCT: dict[str, str] = {
    "autobot-backend/agents/base_agent_types.py::timeout=30.0": "AgentRequest's deadline, not a circuit-breaker call timeout",
    "autobot-backend/config/settings.py::cache_ttl=300": "config-file cache TTL (CONFIG_CACHE_TTL), not the LLM cache TTL",
    "autobot-backend/security/session_posture.py::DEFAULT='default'": "a session ownership posture, not a permission mode",
    "autobot-backend/services/gateway/types.py::CLOSED='closed'": "a gateway session status, not a circuit-breaker state",
    "autobot-backend/services/permission_matcher.py::DEFAULT='default'": "MatchResult 'no rule matched', not a permission mode",
    "autobot-backend/skills/models.py::HTTP='http'": "a skill-repo source kind (git/local/http/mcp), not a network protocol",
    "autobot-backend/type_defs/common.py::DEFAULT='default'": "a chat message type, not a permission mode",
    "autobot-slm-backend/models/manifest.py::retries=3": "a manifest health-probe failure count, not an operation retry budget",
}


def normalise(name: str) -> str:
    """``DEFAULT_TIMEOUT``, ``_timeout`` and ``llm_timeout`` all name a timeout."""
    return re.sub(r"^(llm_|ollama_)", "", re.sub(r"^(default_|_)", "", name, flags=re.IGNORECASE).lower())


def _literal(value: ast.AST | None) -> object | None:
    """The literal a binding sets, reading through ``Field(default=...)``; None when not one."""
    if isinstance(value, ast.Call) and getattr(value.func, "id", getattr(value.func, "attr", "")) == "Field":
        value = next((k.value for k in value.keywords if k.arg == "default"), value.args[0] if value.args else None)
    if not isinstance(value, ast.Constant) or isinstance(value.value, bool):
        return None
    v = value.value
    if isinstance(v, str):
        return v if 3 <= len(v) <= 80 else None
    return v if isinstance(v, int | float) and v not in (0, 1, -1) else None


def bindings(source: str) -> list[tuple[str, object]]:
    """``(name, literal)`` for every single-name assignment in *source* that binds a literal."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        else:
            continue
        literal = _literal(value)
        if literal is not None:
            out.append((name, literal))
    return out


def rehardcodes(sources: dict[str, str]) -> set[str]:
    """``path::name=value`` of every copy, outside the SSOT files, of a value an SSOT file defines.

    The value is part of the key, so a second same-named copy in a recorded file is its own entry.
    """
    groups: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for rel, source in sources.items():
        for name, literal in bindings(source):
            groups[(normalise(name), repr(literal))].add((rel, f"{name}={literal!r}"))
    found = set()
    for members in groups.values():
        files = {rel for rel, _ in members}
        if files & SSOT and len(files) > 1:
            found |= {f"{rel}::{binding}" for rel, binding in members if rel not in SSOT}
    return found


def _live_sources() -> dict[str, str]:
    paths = [p for p in tracked_paths(_ROOT, *(f"{r}/*.py" for r in _ROOTS)) if not _TEST_PATH.search(p)]
    return {p: (_ROOT / p).read_text(encoding="utf-8") for p in paths}


def test_no_ssot_value_is_rehardcoded_on_the_live_tree() -> None:
    sources = _live_sources()
    assert len(sources) >= MIN_FILES, f"only {len(sources)} files scanned -- the enumeration lost its reach"
    found = rehardcodes(sources)
    new = sorted(found - set(PENDING) - set(DISTINCT))
    stale = sorted((set(PENDING) | set(DISTINCT)) - found)
    assert not new, "import the SSOT symbol, or rename a different concept:\n" + "\n".join(new)
    assert not stale, "fixed or gone -- delete the record entry:\n" + "\n".join(stale)


def test_a_planted_copy_of_an_ssot_value_is_caught() -> None:
    sources = {
        "autobot_shared/ssot_constants.py": "class RetryConfig:\n    MAX_RETRIES = 5\n",
        "autobot-backend/x.py": "class C(BaseModel):\n    max_retries: int = Field(default=5, ge=0)\n",
        "autobot-backend/y.py": "_MAX_RETRIES = 5\nmax_retries = 4\nother_name = 5\n",
    }
    assert rehardcodes(sources) == {"autobot-backend/x.py::max_retries=5", "autobot-backend/y.py::_MAX_RETRIES=5"}


def test_a_value_no_ssot_file_defines_is_not_a_rehardcode() -> None:
    sources = {"autobot-backend/a.py": 'STATUS = "failed"\n', "autobot-backend/b.py": 'status = "failed"\n'}
    assert rehardcodes(sources) == set()


def test_every_record_entry_says_why() -> None:
    assert all(reason.strip() for reason in (*PENDING.values(), *DISTINCT.values()))
