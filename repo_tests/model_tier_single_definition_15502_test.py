# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The model tier map has one definition, and its mirrors still equal it (#15502).

`autobot_shared/ssot_config.py` defines the tier models once, as module constants. Several files
hand-copy them because they cannot import Python: the frontend's `LLM_DEFAULTS`, the Ansible
role defaults, and two defaults in `env_registry.py`. This guard parses (never imports) the
canonical file and asserts that

* each of the nine names is assigned exactly once at module level;
* every recorded mirror equals the tier it mirrors;
* the divergences the owner has not yet ruled on are listed in `KNOWN_DIVERGENCES` and still
  diverge, so resolving one forces its entry to be removed rather than lingering;
* no backend module grows a hand-written tier map: a dict literal with two or more values that
  are canonical local model names. Readers use the constants, so none exists today.

Nothing here changes a model value; it only makes a drift fail instead of rotting.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from repo_tests._paths import repo_root
from repo_tests._reach import declare

from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

_SSOT = "autobot_shared/ssot_config.py"
_TIERS = (
    "DEFAULT_LLM_MODEL",
    "DEFAULT_EMBEDDING_MODEL",
    "TRIVIAL_MODEL",
    "ROUTING_MODEL",
    "CLASSIFICATION_MODEL",
    "LIGHT_PROCESSING_MODEL",
    "INSTRUCTION_MODEL",
    "SYSTEM_MODEL",
    "QUALITY_MODEL",
)
_FRONTEND_LLM_DEFAULTS = "autobot-frontend/src/config/llm-defaults.ts"
_FRONTEND_SSOT_CONFIG = "autobot-frontend/src/config/ssot-config.ts"
_AI_STACK = "autobot-slm-backend/ansible/roles/ai-stack/defaults/main.yml"
_AGENT_CONFIG_ROLE = "autobot-slm-backend/ansible/roles/agent_config/defaults/main.yml"
_ENV_REGISTRY = "autobot_shared/env_registry.py"
_AGENTS_YAML = "autobot-infrastructure/shared/config/agents.yaml"
_COMPLETE_YAML = "autobot-infrastructure/shared/config/complete.yaml"
#: The `# <Tier> tier (<model>)` comment ai-stack puts above each group of vars -> the constant it names.
_COMMENT_TIER = {
    "Quality": "QUALITY_MODEL",
    "Instruction": "INSTRUCTION_MODEL",
    "Classification": "CLASSIFICATION_MODEL",
    "Light": "LIGHT_PROCESSING_MODEL",
    "System": "SYSTEM_MODEL",
}
#: env_registry's two hand-copied defaults (they cannot import ssot_config) -> the constant they copy.
_ENV_MIRRORS = {"AUTOBOT_CLASSIFICATION_MODEL": "CLASSIFICATION_MODEL", "AUTOBOT_ORCHESTRATOR_MODEL": "ROUTING_MODEL"}


def _read(rel: str) -> str:
    return (repo_root() / rel).read_text(encoding="utf-8")


def _load_yaml(rel: str) -> Any:
    return yaml.safe_load(_read(rel))


def _module_assignments(source: str) -> dict[str, list[ast.expr]]:
    """Every module-level assignment value per name, descending into if/try/with but not def/class."""
    found: dict[str, list[ast.expr]] = {}

    def visit(stmts: list[ast.stmt]) -> None:
        for node in stmts:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for tgt in targets:
                    if isinstance(tgt, ast.Name):
                        found.setdefault(tgt.id, []).append(node.value)
            elif isinstance(node, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                for body in ("body", "orelse", "finalbody"):
                    visit(getattr(node, body, []))
                for handler in getattr(node, "handlers", []):
                    visit(handler.body)

    visit(ast.parse(source).body)
    return found


def _resolve(assigned: dict[str, list[ast.expr]], name: str) -> str:
    """The string a tier constant holds, following a `NAME = OTHER_NAME` alias."""
    value = assigned[name][-1]
    if isinstance(value, ast.Name):
        return _resolve(assigned, value.id)
    assert isinstance(value, ast.Constant) and isinstance(value.value, str), f"{name} is not a string literal"
    return value.value


@functools.lru_cache(maxsize=1)
def _canonical() -> dict[str, str]:
    assigned = _module_assignments(_read(_SSOT))
    return {name: _resolve(assigned, name) for name in _TIERS}


def _local_models() -> frozenset[str]:
    return frozenset(_canonical().values())


def _bare(model: str) -> str:
    """`nomic-embed-text:latest` and `nomic-embed-text` are the same Ollama model."""
    return model.removesuffix(":latest")


# --- (a) one definition ----------------------------------------------------


def test_each_tier_name_is_assigned_exactly_once() -> None:
    assigned = _module_assignments(_read(_SSOT))
    counts = {name: len(assigned.get(name, [])) for name in _TIERS}
    assert counts == dict.fromkeys(_TIERS, 1), f"#15502: tier names must be assigned once in {_SSOT}: {counts}"


def test_the_assignment_counter_sees_a_second_definition() -> None:
    twice = 'TRIVIAL_MODEL = "a"\nif True:\n    TRIVIAL_MODEL = "b"\n'
    assert len(_module_assignments(twice)["TRIVIAL_MODEL"]) == 2
    assert len(_module_assignments('def f():\n    TRIVIAL_MODEL = "b"\n').get("TRIVIAL_MODEL", [])) == 0


# --- (b) mirrors -----------------------------------------------------------


def _frontend_defaults() -> dict[str, str]:
    body = re.search(r"export const LLM_DEFAULTS = \{(.*?)\} as const", _read(_FRONTEND_LLM_DEFAULTS), re.S)
    assert body, f"LLM_DEFAULTS not found in {_FRONTEND_LLM_DEFAULTS}"
    return dict(re.findall(r"(\w+):\s*'([^']+)'", body.group(1)))


def test_frontend_llm_defaults_equal_the_ssot() -> None:
    """Mirror: TypeScript cannot import Python; ssot-parity.spec.ts checks the same pair in vitest."""
    assert _frontend_defaults() == {
        "defaultModel": _canonical()["DEFAULT_LLM_MODEL"],
        "embeddingModel": _canonical()["DEFAULT_EMBEDDING_MODEL"],
    }


def test_ssot_config_ts_holds_no_model_name_literal() -> None:
    text = _read(_FRONTEND_SSOT_CONFIG)
    held = sorted(m for m in _local_models() if m in text)
    assert not held, f"#15502: {_FRONTEND_SSOT_CONFIG} hardcodes {held}; read them from LLM_DEFAULTS"


def _ai_stack_vars() -> list[tuple[str, str, str]]:
    """`(var, value, tier constant)` for every `ai_*_model`, tier read from the file's own comment."""
    out: list[tuple[str, str, str]] = []
    tier = ""
    for line in _read(_AI_STACK).splitlines():
        comment = re.match(r"# (\w+) tier \(([^)]+)\)", line)
        if comment:
            assert _canonical()[_COMMENT_TIER[comment.group(1)]] == comment.group(2), f"stale comment: {line}"
            tier = _COMMENT_TIER[comment.group(1)]
        var = re.match(r'(ai_\w+_model):\s*"([^"]+)"', line)
        if var:
            out.append((var.group(1), var.group(2), tier))
    return out


def test_ai_stack_model_vars_equal_their_tier() -> None:
    """Mirror: Ansible defaults cannot import ssot_config; the file's own tier comments map var -> tier."""
    rows = _ai_stack_vars()
    assert len(rows) == 15, f"expected 15 ai_*_model vars, parsed {len(rows)}"
    bad = {v: (val, _canonical()[t]) for v, val, t in rows if val != _canonical()[t]}
    assert not bad, f"#15502: ai-stack vars differ from their tier (var: (value, canonical)): {bad}"


def test_agent_config_ollama_models_are_the_canonical_local_models() -> None:
    """Mirror: the provisioning list must pull exactly the models the tiers can select."""
    listed = {_bare(m) for m in _load_yaml(_AGENT_CONFIG_ROLE)["ollama_models"]}
    assert listed == {_bare(m) for m in _local_models()}


def _env_registry_defaults() -> dict[str, str]:
    out: dict[str, str] = {}
    for call in ast.walk(ast.parse(_read(_ENV_REGISTRY))):
        if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "EnvVarSpec":
            kw = {k.arg: k.value for k in call.keywords}
            name, default = kw.get("name"), kw.get("default")
            if isinstance(name, ast.Constant) and isinstance(default, ast.Constant):
                out[name.value] = default.value
    return out


def test_env_registry_hand_copied_defaults_equal_the_ssot() -> None:
    """Mirror: env_registry cannot import ssot_config at registration time (#14856)."""
    defaults = _env_registry_defaults()
    got = {name: defaults.get(name) for name in _ENV_MIRRORS}
    assert got == {name: _canonical()[const] for name, const in _ENV_MIRRORS.items()}


# --- (c) known divergences, pending owner decisions ------------------------

_PENDING = "pending the #15502 owner decision"
#: `(file, dotted path)` -> `(value held today, what it diverges from, reason)`.
KNOWN_DIVERGENCES: dict[tuple[str, str], tuple[str, str, str]] = {
    **{
        (_AGENTS_YAML, f"llm.models.{role}"): (
            "phi3:mini",
            "mistral:7b-instruct",
            f"agents.yaml puts this role on the light tier; ai-stack's ai_{role}_model is the instruction tier; {_PENDING}",
        )
        for role in ("kb_librarian", "knowledge_extraction", "knowledge_retrieval")
    },
    **{
        (_COMPLETE_YAML, f"llm.models.{role}"): (value, "no canonical tier", f"{_PENDING}; owned by issue-17599")
        for role, value in (
            ("rag", "gemma3:270m"),
            ("chat", "llama3.2:1b-instruct-q4_K_M"),
            ("classification", "gemma3:270m"),
        )
    },
    **{
        (_COMPLETE_YAML, f"agents.{role}.model"): (
            "llama3.2:1b-instruct-q4_K_M",
            "no canonical tier",
            f"{_PENDING}; owned by issue-17599",
        )
        for role in ("kb_librarian", "research", "chat", "system_commands", "security_scanner", "code_analysis")
    },
    (_COMPLETE_YAML, "agent_global.default_model"): (
        "llama3.2:1b-instruct-q4_K_M",
        "no canonical tier",
        f"{_PENDING}; owned by issue-17599",
    ),
}


def _dig(doc: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        doc = doc[part]
    return doc


def _ai_stack_value(role: str) -> str:
    return {v: val for v, val, _ in _ai_stack_vars()}[f"ai_{role}_model"]


@pytest.mark.parametrize(("file", "path"), sorted(KNOWN_DIVERGENCES))
def test_a_known_divergence_still_diverges(file: str, path: str) -> None:
    """Stale entry: once a value is reconciled, the entry must be deleted, not left to rot."""
    value, against, reason = KNOWN_DIVERGENCES[file, path]
    live = _dig(_load_yaml(file), path)
    assert (
        live == value
    ), f"{file}:{path} is now {live!r}, not the recorded {value!r}; update or remove the entry ({reason})"
    if against == "no canonical tier":
        assert live not in _local_models(), f"{file}:{path} now names a canonical model; remove this entry"
    else:
        other = _ai_stack_value(path.rsplit(".", 1)[-1])
        assert other == against and live != other, f"{file}:{path} converged; remove this entry"


# --- (d) no new hand-written tier map --------------------------------------


def _tier_maps(source: str, models: frozenset[str]) -> list[int]:
    """Line numbers of dict literals (or `dict(k=v)` calls) with >= 2 values naming a canonical model."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            values = node.values
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "dict":
            values = [kw.value for kw in node.keywords]
        else:
            continue
        named = [v for v in values if isinstance(v, ast.Constant) and v.value in models]
        if len(named) >= 2:
            hits.append(node.lineno)
    return hits


def _is_source_python(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return rel.endswith(".py") and not (name.endswith("_test.py") or name.startswith("test_") or "/tests/" in rel)


def _source_population(root: Path) -> list[str]:
    try:
        tracked = tracked_paths(root, "autobot-backend", "autobot-slm-backend", "autobot_shared")
    except EmptyEnumeration:
        return []
    return [rel for rel in tracked if _is_source_python(rel)]


REACH = declare(
    "model-tier-map-scan",
    discover=_source_population,
    # Mid-window, from REACH.window(); the arithmetic is on the PR, not here.
    floor=2847,
    what="non-test python source",
    roots=("autobot-backend", "autobot-slm-backend", "autobot_shared"),
    growth=300,
)


def test_a_tier_map_is_flagged_and_a_reference_is_not() -> None:
    models = _local_models()
    assert _tier_maps('M = {"simple": "gemma2:2b", "complex": "qwen3.5:9b"}\n', models) == [1]
    assert _tier_maps('M = dict(simple="gemma2:2b", complex="qwen3.5:9b")\n', models) == [1]
    assert _tier_maps('M = {"x": TRIVIAL_MODEL, "y": QUALITY_MODEL}\n', models) == []
    assert _tier_maps('M = {"x": "qwen3.5:9b", "y": "not-a-model"}\n', models) == []


def test_the_map_scan_is_not_vacuous() -> None:
    """The scan reads the files that hold the literals, and finds the literals it hunts for."""
    population = REACH.examined(repo_root())
    assert _SSOT in population and _ENV_REGISTRY in population
    literal_holders = [r for r in population if any(m in _read(r) for m in _local_models())]
    assert _SSOT in literal_holders, "the canonical file's own literals were not seen"


def test_no_module_hand_writes_a_tier_map() -> None:
    models = _local_models()
    found = {r: lines for r in REACH.examined(repo_root()) if (lines := _tier_maps(_read(r), models))}
    assert not found, f"#15502: hand-written tier map(s) {found}; import the tier constants from {_SSOT}"


_READERS = (
    "autobot-backend/config/registry_defaults.py",
    "autobot-backend/llm_shared/tiered_routing/tier_config.py",
    "autobot-backend/api/agent_config.py",
    "autobot-slm-backend/agent_seed_roster.py",
    "autobot-slm-backend/services/capability_tiers.py",
)


@pytest.mark.parametrize("rel", _READERS)
def test_a_canonical_reader_imports_the_ssot_and_holds_no_model_literal(rel: str) -> None:
    source = _read(rel)
    assert "autobot_shared.ssot_config" in source, f"{rel} no longer reads the SSOT"
    literals = [
        n.value for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Constant) and n.value in _local_models()
    ]
    assert not literals, f"#15502: {rel} holds model literal(s) {sorted(set(literals))}; use the constants"
