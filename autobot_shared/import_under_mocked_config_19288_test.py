# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Guard: every ``autobot_shared`` module imports under a fully mocked config manager (#19288).

Reproduces the SLM test harness (``autobot-slm-backend/conftest.py`` and the hermetic suites that
set ``sys.modules["config"]`` to a stub): ``config`` is a ``MagicMock``, so every
``config_manager.get_nested(...)`` the logging manager makes returns a ``MagicMock``. Before #19288
that made ``get_logger(__name__)`` raise at import, so a module calling it at module level broke
every harness that imported it, and modules took to stdlib ``logging`` to dodge it.

Runs in a subprocess: the sweep needs a ``sys.modules`` and a ``LoggingManager`` cache that no
earlier test has already populated with real config.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: Modules that cannot be imported standalone, each with its reason. A module listed here is not
#: checked; keep the reason specific so the entry can be removed once it no longer holds.
_EXCLUDED: dict[str, str] = {}

_SWEEP = """
import importlib, json, pkgutil, sys
from unittest.mock import MagicMock

sys.modules["config"] = MagicMock()
excluded = set(json.loads(sys.argv[1]))
failures = {}
try:
    import autobot_shared
except BaseException as exc:
    print(json.dumps({"autobot_shared": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(0)

def _is_test(name):
    leaf = name.rsplit(".", 1)[-1]
    return leaf.endswith("_test") or leaf.startswith("test_") or leaf == "conftest" or ".tests" in name

names = [m.name for m in pkgutil.walk_packages(autobot_shared.__path__, "autobot_shared.")]
targets = sorted(n for n in names if not _is_test(n) and n not in excluded)
for name in targets:
    try:
        importlib.import_module(name)
    except BaseException as exc:
        failures[name] = f"{type(exc).__name__}: {exc}"
print(json.dumps({"checked": len(targets), "failures": failures}))
"""


def _run_sweep(cwd: Path) -> dict[str, object]:
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT), "AUTOBOT_LOGS_DIR": str(cwd / "logs")}
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and inline script, no user input
        [sys.executable, "-c", _SWEEP, json.dumps(sorted(_EXCLUDED))],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, f"sweep crashed:\n{result.stderr[-4000:]}"
    report: dict[str, object] = json.loads(result.stdout.strip().splitlines()[-1])
    return report


def test_every_autobot_shared_module_imports_under_a_mocked_config(tmp_path: Path) -> None:
    report = _run_sweep(tmp_path)
    assert "autobot_shared" not in report, f"the package itself failed to import: {report}"
    checked = report["checked"]
    assert isinstance(checked, int) and checked > 100, f"sweep found too few modules to be real: {checked}"
    assert report["failures"] == {}, json.dumps(report["failures"], indent=2)
    # A MagicMock's __fspath__ yields "MagicMock/..."; it must never become a log directory.
    assert not (tmp_path / "MagicMock").exists()
