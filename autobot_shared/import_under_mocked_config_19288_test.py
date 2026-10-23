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
_SLM_ROOT = _REPO_ROOT / "autobot-slm-backend"

#: Modules that cannot be imported standalone, each with its reason. A module listed here is not
#: checked; keep the reason specific so the entry can be removed once it no longer holds.
_EXCLUDED: dict[str, str] = {}

#: SLM-backend modules that used stdlib logging only because of #19288 and now call get_logger at
#: import. Imported with the SLM backend root on sys.path, as its conftest arranges.
_SLM_MOVED = (
    "api._pricing_post_sync",
    "api._resume_plan",
    "api._rsync_paths",
    "api.code_sync_paths",
    "api.memory_lifecycle_proxy",
    "api.venv_reconcile",
    "services.admin_password_sync",
    "services.auth",
    "services.frontend_bundle_health",
    "services.full_tree_drift",
    "services.generated_system_secrets",
    "services.git_subprocess",
    "services.process_divergence",
    "services.service_remediation_tracker",
    "services.sync_deletions",
)

# sys.modules["config"] = MagicMock() is exactly what the SLM conftest's _stub("config") installs.
_SWEEP = """
import importlib, json, pkgutil, sys
from unittest.mock import MagicMock

sys.modules["config"] = MagicMock()
spec = json.loads(sys.argv[1])
try:
    import autobot_shared
except BaseException as exc:
    print(json.dumps({"autobot_shared": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(0)

def _is_test(name):
    leaf = name.rsplit(".", 1)[-1]
    return leaf.endswith("_test") or leaf.startswith("test_") or leaf == "conftest" or ".tests" in name

if spec["modules"]:
    targets = spec["modules"]
else:
    names = [m.name for m in pkgutil.walk_packages(autobot_shared.__path__, "autobot_shared.")]
    targets = sorted(n for n in names if not _is_test(n) and n not in set(spec["excluded"]))
failures = {}
for name in targets:
    try:
        importlib.import_module(name)
    except BaseException as exc:
        failures[name] = f"{type(exc).__name__}: {exc}"
print(json.dumps({"checked": len(targets), "failures": failures}))
"""


def _run_sweep(cwd: Path, modules: tuple[str, ...] = (), roots: tuple[Path, ...] = ()) -> dict[str, object]:
    """Run the import sweep in a fresh interpreter: *modules*, or every autobot_shared module."""
    pythonpath = os.pathsep.join(str(p) for p in (*roots, _REPO_ROOT))
    env = {**os.environ, "PYTHONPATH": pythonpath, "AUTOBOT_LOGS_DIR": str(cwd / "logs")}
    spec = json.dumps({"modules": list(modules), "excluded": sorted(_EXCLUDED)})
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and inline script, no user input
        [sys.executable, "-c", _SWEEP, spec],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, f"sweep crashed:\n{result.stderr[-4000:]}"
    report: dict[str, object] = json.loads(result.stdout.strip().splitlines()[-1])
    assert "autobot_shared" not in report, f"the package itself failed to import: {report}"
    # A MagicMock's __fspath__ yields "MagicMock/..."; it must never become a log directory.
    assert not (cwd / "MagicMock").exists()
    return report


def test_every_autobot_shared_module_imports_under_a_mocked_config(tmp_path: Path) -> None:
    report = _run_sweep(tmp_path)
    checked = report["checked"]
    assert isinstance(checked, int) and checked > 100, f"sweep found too few modules to be real: {checked}"
    assert report["failures"] == {}, json.dumps(report["failures"], indent=2)


def test_slm_modules_moved_to_get_logger_import_under_a_mocked_config(tmp_path: Path) -> None:
    report = _run_sweep(tmp_path, _SLM_MOVED, (_SLM_ROOT,))
    assert report["checked"] == len(_SLM_MOVED)
    assert report["failures"] == {}, json.dumps(report["failures"], indent=2)
