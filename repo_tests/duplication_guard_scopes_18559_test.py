# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""The duplication guard's scopes, and the job that lowers their pins (#18559, #18461, #16324).

The guard is only as wide as the paths its steps name. Dropping one (or a whole step) turns a
tree back into one nothing scans, and every other test still passes, so the scope list is held
here as a contract: each step, the trees it scans, its pin variable and its log. The fork
ratchet's roots are held the same way, because #18559 widened them for the same reason.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from repo_tests._paths import repo_root

from autobot_shared.symbol_forks import FORK_ROOTS

WORKFLOW = Path(".github/workflows/duplication-guard.yml")

#: step id -> (trees jscpd scans, pin variable, log the step tees)
EXPECTED_SCOPES = {
    "main_scope": (("autobot-backend", "autobot-frontend/src"), "MAX_DUP_LINES", "main-dup.log"),
    "slm_scope": (
        ("autobot_shared", "autobot-slm-backend", "autobot-slm-frontend/src"),
        "SLM_MAX_DUP_LINES",
        "slm-gate.log",
    ),
    "tooling_scope": (("scripts", "tools", "pipeline-scripts"), "TOOLING_MAX_DUP_LINES", "tooling-gate.log"),
    "cross_gui_scope": (
        ("autobot-frontend/src", "autobot-slm-frontend/src", "autobot-plugins", "libs/autobot-ui"),
        "CROSS_GUI_MAX_DUP_LINES",
        "cross-gui-gate.log",
    ),
    "cross_backend_scope": (
        ("autobot-backend", "autobot-slm-backend", "autobot_shared", "plugins"),
        "CROSS_BACKEND_MAX_DUP_LINES",
        "cross-backend-gate.log",
    ),
}

#: Roots #18559 added to the symbol-fork ratchet; it must keep reading all of them.
REQUIRED_FORK_ROOTS = (
    "autobot-backend",
    "autobot-slm-backend",
    "autobot_shared",
    "autobot-npu-worker",
    "autobot-tts-worker",
    "autobot-browser-worker",
    "autobot-infrastructure",
    "scripts",
    "tools",
    "pipeline-scripts",
)

_SPEC = importlib.util.spec_from_file_location(
    "duplication_pin_lowering", repo_root() / "scripts" / "duplication_pin_lowering.py"
)
lowering = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = lowering
_SPEC.loader.exec_module(lowering)


def _document() -> dict:
    return yaml.safe_load((repo_root() / WORKFLOW).read_text(encoding="utf-8"))


def _steps() -> dict[str, dict]:
    job = _document()["jobs"]["duplication-guard"]
    return {step["id"]: step for step in job["steps"] if "id" in step}


def _scanned_trees(run: str) -> tuple[str, ...]:
    """The positional paths between ``jscpd@...`` and the first flag."""
    head = run.split('"jscpd@${JSCPD_VERSION}"', 1)[1].split(" -k ", 1)[0]
    return tuple(head.replace("\\", " ").split())


def _flags(run: str) -> str:
    return run.split(" -k ", 1)[1].split("|", 1)[0]


def test_every_scope_scans_exactly_its_trees_and_is_gated_on_its_pin() -> None:
    steps = _steps()
    for step_id, (trees, pin_var, log) in EXPECTED_SCOPES.items():
        assert step_id in steps, f"scope step {step_id} is gone from {WORKFLOW}"
        run = steps[step_id]["run"]
        assert _scanned_trees(run) == trees, f"{step_id} no longer scans {trees}"
        assert f"tee /tmp/{log}" in run, f"{step_id} no longer tees /tmp/{log}"
        assert f'duplication_gate.py /tmp/{log} "${{{pin_var}}}"' in run, f"{step_id} is not gated on {pin_var}"


def test_no_scope_step_is_added_without_joining_the_contract() -> None:
    gating = {sid for sid, step in _steps().items() if "duplication_gate.py" in step.get("run", "")}
    assert gating == set(EXPECTED_SCOPES), sorted(gating ^ set(EXPECTED_SCOPES))


def test_every_scope_uses_the_same_jscpd_flags() -> None:
    """A scope measured with other flags is a different number under the same name."""
    steps = _steps()
    reference = _flags(steps["main_scope"]["run"])
    for step_id in EXPECTED_SCOPES:
        assert _flags(steps[step_id]["run"]) == reference, f"{step_id} has different jscpd flags"


def test_every_pin_is_a_measured_count() -> None:
    env = _document()["jobs"]["duplication-guard"]["env"]
    for _trees, pin_var, _log in EXPECTED_SCOPES.values():
        assert re.fullmatch(r"[0-9]+", str(env[pin_var])), f"{pin_var}={env[pin_var]!r} is not a count"


def test_a_change_under_every_scanned_tree_triggers_the_guard() -> None:
    on = _document()[True]  # PyYAML reads the key `on` as a boolean
    for event in ("pull_request", "push"):
        paths = on[event]["paths"]
        for step_id, (trees, _pin, _log) in EXPECTED_SCOPES.items():
            for tree in trees:
                assert any(p.startswith(tree.rstrip("/") + "/") for p in paths), f"{event}: nothing triggers on {tree}"


def test_the_lowering_table_matches_the_workflow() -> None:
    table = {(scope.pin_var, scope.log_name) for scope in lowering.SCOPES}
    assert table == {(pin, log) for _t, pin, log in EXPECTED_SCOPES.values()}


def test_the_fork_ratchet_still_reads_every_widened_root() -> None:
    assert set(REQUIRED_FORK_ROOTS) <= set(FORK_ROOTS), sorted(set(REQUIRED_FORK_ROOTS) - set(FORK_ROOTS))


def test_the_stale_deferral_comment_is_gone() -> None:
    text = (repo_root() / WORKFLOW).read_text(encoding="utf-8")
    assert "deferred" not in text.split("name: duplication-guard", 1)[0]
    assert "#9856 and #9859, named here before, are closed" in text


# --- the pin-lowering job (#16324) ---------------------------------------------------------

_ROW = "│ Total: │ 100 │ 5000 │ 40000 │ 7 │ {dup} (1%) │ 0 │"
_ENV = "    env:\n      MAX_DUP_LINES: '500'\n      SLM_MAX_DUP_LINES: '100'\n"


def test_the_job_runs_only_on_a_green_push_and_never_pushes_main() -> None:
    job = _document()["jobs"]["lower-pins"]
    assert job["needs"] == "duplication-guard"
    assert "github.event_name == 'push'" in job["if"] and "result == 'success'" in job["if"]
    assert job["permissions"] == {"contents": "write", "pull-requests": "write"}
    run = "\n".join(step.get("run", "") for step in job["steps"])
    assert "git push --force-with-lease" in run and 'origin "$BRANCH"' in run
    assert not re.search(r"git push[^\n]*\bmain\b", run), "the job must open a PR, never push main"


def test_a_pin_above_its_count_is_lowered_to_it() -> None:
    text, changes = lowering.lower_pins(_ENV, {"MAX_DUP_LINES": 480, "SLM_MAX_DUP_LINES": 100}, "the-merge-commit")
    assert changes == [("MAX_DUP_LINES", 500, 480)]
    assert "      MAX_DUP_LINES: '480'\n" in text and "SLM_MAX_DUP_LINES: '100'" in text
    assert "# 500 -> 480 (#16324)" in text and "the-merge-commit" in text


def test_a_pin_is_never_raised() -> None:
    text, changes = lowering.lower_pins(_ENV, {"MAX_DUP_LINES": 501, "SLM_MAX_DUP_LINES": 100})
    assert changes == [] and text == _ENV


def test_the_two_names_that_share_a_suffix_are_not_confused() -> None:
    text, _ = lowering.lower_pins(_ENV, {"SLM_MAX_DUP_LINES": 90})
    assert "      MAX_DUP_LINES: '500'\n" in text and "      SLM_MAX_DUP_LINES: '90'\n" in text


def _write_logs(directory: Path, dup: int) -> None:
    for scope in lowering.SCOPES:
        (directory / scope.log_name).write_text(_ROW.format(dup=dup), encoding="utf-8")


def test_the_cli_lowers_the_real_workflow_from_real_logs(tmp_path: Path) -> None:
    workflow = tmp_path / "wf.yml"
    workflow.write_text((repo_root() / WORKFLOW).read_text(encoding="utf-8"), encoding="utf-8")
    _write_logs(tmp_path, 7)
    done = subprocess.run(  # nosec B603  # fixed argv, no shell
        [
            sys.executable,
            "-I",
            str(repo_root() / "scripts" / "duplication_pin_lowering.py"),
            str(workflow),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert len(done.stdout.splitlines()) == len(lowering.SCOPES)
    env = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["duplication-guard"]["env"]
    assert all(str(env[scope.pin_var]) == "7" for scope in lowering.SCOPES)


def test_an_unreadable_log_touches_no_pin(tmp_path: Path) -> None:
    workflow = tmp_path / "wf.yml"
    workflow.write_text(_ENV, encoding="utf-8")
    _write_logs(tmp_path, 1)
    (tmp_path / lowering.SCOPES[1].log_name).unlink()
    assert lowering.main([str(workflow), str(tmp_path)]) == 2
    assert workflow.read_text(encoding="utf-8") == _ENV


@pytest.mark.parametrize("pin", ["UNMEASURED", "12.5", ""])
def test_a_pin_that_is_not_a_count_is_left_alone(pin: str) -> None:
    text = f"      MAX_DUP_LINES: '{pin}'\n"
    assert lowering.lower_pins(text, {"MAX_DUP_LINES": 1}) == (text, [])
