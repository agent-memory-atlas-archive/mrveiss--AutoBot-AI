# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The #18094 section-2 copied constants each have ONE home; a second definition fails.

Homes: the install root, the agent dir and the agent heartbeat interval in
``autobot_shared/fleet_constants.py``; the semantic similarity bands in
``autobot-backend/code_intelligence/similarity_thresholds.py``.

The env-fork sweep (``tools/lint/_env_fork_scan.py``) already flags a MODULE-LEVEL literal constant that
restates a ``fleet_constants`` name. This file pins what that sweep cannot see:

1. a similarity band assigned anywhere but its owner -- including inside ``try``/``except``, where the
   ``0.7`` fallbacks lived -- or restated as a conditional fallback (``SIMILARITY_MEDIUM if ok else 0.7``);
2. a bare ``"/opt/autobot"`` string literal anywhere outside ``fleet_constants``;
3. the SLM settings restating the heartbeat interval as a literal;
4. the Ansible copies (``autobot.base_dir``, ``slm_agent_dir``, ``slm_heartbeat_interval``) disagreeing.

The contrast tests rebuild each shape #18094 found and require the detector to flag it, so a clean live
run cannot come from a detector that lost its teeth.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml
from repo_tests._paths import repo_root

from autobot_shared import fleet_constants
from tools.lint import check_getenv_ssot_drift  # noqa: F401 -- puts tools/lint on sys.path for the sibling import

from tools.lint import _env_fork_scan as scan_mod  # isort: skip

_ROOT = repo_root()
_FLEET_HOME = "autobot_shared/fleet_constants.py"
_SIMILARITY_HOME = "autobot-backend/code_intelligence/similarity_thresholds.py"
_SIMILARITY_NAMES = frozenset({"SIMILARITY_HIGH", "SIMILARITY_MEDIUM", "SIMILARITY_LOW"})
_SLM_SETTINGS = "autobot-slm-backend/config.py"
_ANSIBLE = "autobot-slm-backend/ansible"

#: Bare install-root literals that predate this guard, each owned by the issue named. Shrink-only.
KNOWN_BARE_ROOT: dict[str, str] = {
    "autobot-slm-backend/services/code_distributor.py": "#15708 (SLM_REPO_PATH, two modules disagree)",
}

#: Files the sweep must reach, or a clean run says nothing: each home and each former restatement.
_WITNESSES = (
    _FLEET_HOME,
    _SIMILARITY_HOME,
    _SLM_SETTINGS,
    "autobot-backend/code_intelligence/shared/analysis_base.py",
    "autobot-slm-backend/services/role_registry.py",
    "autobot-slm-backend/slm/agent/agent.py",
    "autobot-npu-worker/resources/windows-npu-worker/app/utils/backend_telemetry.py",
)


def _sources(root: Path) -> dict[str, str]:
    """Tracked production ``.py`` under the env-fork sweep's roots -- the same population and test rule."""
    out: dict[str, str] = {}
    for rel in scan_mod.tracked_paths(root, "*.py"):
        if rel.split("/")[0] not in scan_mod.SCAN_ROOTS or rel.endswith("_test.py") or "/tests/" in rel:
            continue
        try:
            out[rel] = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    return out


def _is_number(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)


def _band_name(node: ast.AST) -> str | None:
    return node.id if isinstance(node, ast.Name) and node.id in _SIMILARITY_NAMES else None


def similarity_restatements(rel: str, source: str) -> list[str]:
    """A band assigned outside its home (at any depth), or paired with a literal fallback."""
    out: list[str] = []
    for node in ast.walk(ast.parse(source)):
        targets = node.targets if isinstance(node, ast.Assign) else []
        targets = [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else targets
        for name in filter(None, map(_band_name, targets)):
            if rel != _SIMILARITY_HOME:
                out.append(f"{rel}:{node.lineno}: assigns {name} -- import it from {_SIMILARITY_HOME}")
        if isinstance(node, ast.IfExp):
            pair = (node.body, node.orelse)
            name = _band_name(pair[0]) or _band_name(pair[1])
            if name and (_is_number(pair[0]) or _is_number(pair[1])):
                out.append(f"{rel}:{node.lineno}: {name} with a literal fallback")
    return out


def bare_root_literals(rel: str, source: str) -> list[str]:
    """Every ``"/opt/autobot"`` string constant outside ``fleet_constants``."""
    if rel == _FLEET_HOME:
        return []
    root = fleet_constants.FLEET_INSTALL_ROOT
    return [
        f"{rel}:{node.lineno}: the install root as a literal -- import FLEET_INSTALL_ROOT from {_FLEET_HOME}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and node.value == root
    ]


def settings_heartbeat_literals(rel: str, source: str) -> list[str]:
    """An SLM-side ``heartbeat_interval`` field whose default is a literal, not the shared constant."""
    if not rel.startswith("autobot-slm-backend/"):
        return []
    return [
        f"{rel}:{node.lineno}: heartbeat_interval restated -- use DEFAULT_HEARTBEAT_INTERVAL from {_FLEET_HOME}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "heartbeat_interval"
        and _is_number(node.value)
    ]


def findings(sources: dict[str, str]) -> list[str]:
    """Every restatement in *sources*, minus the shrink-only bare-root baseline."""
    out: list[str] = []
    for rel, source in sorted(sources.items()):
        out += similarity_restatements(rel, source) + settings_heartbeat_literals(rel, source)
        if rel not in KNOWN_BARE_ROOT:
            out += bare_root_literals(rel, source)
    return out


def test_each_value_has_one_home_on_the_live_tree() -> None:
    sources = _sources(_ROOT)
    missing = [rel for rel in _WITNESSES if rel not in sources]
    assert missing == [], f"the sweep never reached {missing} -- a clean result would be blind"
    found = findings(sources)
    assert found == [], f"checked {len(sources)} files: {len(found)} restatement(s)\n" + "\n".join(found)


def test_the_bare_root_baseline_only_shrinks() -> None:
    sources = _sources(_ROOT)
    stale = [rel for rel in KNOWN_BARE_ROOT if not bare_root_literals(rel, sources.get(rel, ""))]
    assert stale == [], f"{stale} no longer state the literal -- delete the KNOWN_BARE_ROOT entry"


def _yaml(rel: str) -> dict:
    return yaml.safe_load((_ROOT / _ANSIBLE / rel).read_text(encoding="utf-8"))


def test_the_ansible_copies_agree_with_fleet_constants() -> None:
    base_dir = _yaml("inventory/group_vars/all.yml")["autobot"]["base_dir"]
    agent = _yaml("roles/slm_agent/defaults/main.yml")
    agent_suffix = agent["slm_agent_dir"].replace("{{ autobot.base_dir }}", "")

    assert base_dir == fleet_constants.FLEET_INSTALL_ROOT
    assert fleet_constants.FLEET_AGENT_DIR == fleet_constants.FLEET_INSTALL_ROOT + agent_suffix
    assert agent["slm_heartbeat_interval"] == fleet_constants.DEFAULT_HEARTBEAT_INTERVAL


# --- contrast: each shape #18094 found must be flagged -------------------------------------------


def test_a_try_except_band_fallback_is_flagged() -> None:
    source = "try:\n    from x import SIMILARITY_MEDIUM\nexcept ImportError:\n    SIMILARITY_MEDIUM = 0.7\n"
    assert len(findings({"autobot-backend/code_intelligence/shared/analysis_base.py": source})) == 1


def test_a_conditional_band_fallback_is_flagged() -> None:
    source = "f(min_similarity=(SIMILARITY_MEDIUM if HAS else 0.7))\n"
    assert len(findings({"autobot-backend/code_intelligence/security/analyzer.py": source})) == 1


def test_the_owner_may_define_its_bands() -> None:
    assert findings({_SIMILARITY_HOME: "SIMILARITY_MEDIUM = 0.70\n"}) == []


def test_a_restated_install_root_fallback_is_flagged() -> None:
    source = 'import os\n_BASE = os.environ.get("AUTOBOT_BASE_DIR", "/opt/autobot")\n'
    assert len(findings({"autobot-slm-backend/services/role_registry.py": source})) == 1


def test_a_literal_settings_heartbeat_is_flagged() -> None:
    source = "class Settings:\n    heartbeat_interval: int = 30\n"
    assert len(findings({_SLM_SETTINGS: source})) == 1
