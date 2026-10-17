# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The uninstaller covers what AutoBot deploys and removes no account it did not create (#18653).

`install.sh --uninstall` used to stop halfway (it deleted its own log directory and died on
the next write), knew about only part of what the playbooks install, and ran
`userdel -r autobot` unconditionally -- on a reused machine that account was the owner's own
uid-1000 login. It is now scripts/uninstall.sh. These pin:

- coverage: every systemd unit, sudoers file and apt source the Ansible roles deploy under a
  literal name is matched by the uninstaller's lists (a new role component cannot be missed);
- the install record: every key install.sh records is one the uninstaller reads;
- accounts: the real plan_users() selects an account only from the record or AutoBot's own
  admin marker, never by name;
- the log sits outside every path it removes.

**What these do not cover.** Templated names (``{{ var }}``) are not resolved, and nothing here
runs an uninstall on a real host -- a run on a host with a prior install is the end-to-end proof.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from repo_tests._paths import repo_root

from tools.lint._scan_helpers import tracked_paths

_ROOT = repo_root()
_UNINSTALL = _ROOT / "scripts" / "uninstall.sh"
_INSTALL = _ROOT / "install.sh"


def _array(name: str) -> list[str]:
    match = re.search(rf"^readonly {name}=\((.*?)\)", _UNINSTALL.read_text(encoding="utf-8"), re.S | re.M)
    assert match, f"readonly {name}=(...) not found in scripts/uninstall.sh"
    return [tok.strip("\"'") for tok in match.group(1).split()]


def _role_files() -> list[Path]:
    files = [_ROOT / p for p in tracked_paths(_ROOT, "autobot-slm-backend/ansible/*.yml")]
    assert len(files) > 100, f"read {len(files)} ansible files -- the scan is broken"
    return files


def _role_dests(prefix: str) -> set[str]:
    """Literal destinations under `prefix` that any role or playbook writes."""
    found = set()
    pattern = re.compile(rf"""dest:\s*["']?({re.escape(prefix)}[^"'\s]+)""")
    for path in _role_files():
        found |= {m.group(1) for m in pattern.finditer(path.read_text(encoding="utf-8")) if "{{" not in m.group(1)}
    return found


def test_every_deployed_systemd_unit_is_removed() -> None:
    units, globs, dropins = set(_array("UNITS")), _array("UNIT_GLOBS"), set(_array("UNIT_DROPINS"))
    deployed = _role_dests("/etc/systemd/system/")
    assert len(deployed) >= 20, f"read {len(deployed)} deployed units -- the scan is broken"
    missed = []
    for dest in sorted(deployed):
        name = dest.removeprefix("/etc/systemd/system/")
        if "/" in name:  # a drop-in under <unit>.d/
            covered = dest in dropins
        else:
            covered = name in units or any(fnmatch.fnmatch(name, g) for g in globs)
        if not covered:
            missed.append(dest)
    assert not missed, f"roles deploy units scripts/uninstall.sh never removes: {missed}"


def test_every_deployed_sudoers_file_is_removed() -> None:
    covered = set(_array("SUDOERS")) | {"autobot"}  # autobot: install.sh's own, via created_sudoers
    deployed = {d.removeprefix("/etc/sudoers.d/") for d in _role_dests("/etc/sudoers.d/")}
    assert deployed, "no sudoers destinations read -- the scan is broken"
    assert (
        not deployed - covered
    ), f"roles deploy sudoers files scripts/uninstall.sh never removes: {sorted(deployed - covered)}"


def test_every_apt_source_a_role_adds_is_removed() -> None:
    sources = _array("APT_SOURCES")
    added = set()
    for path in _role_files():
        added |= set(re.findall(r"""apt_repo_filename:\s*["']([a-z0-9-]+)["']""", path.read_text(encoding="utf-8")))
    assert added, "no apt_repo_filename read -- the scan is broken"
    missed = sorted(a for a in added if not any(a.startswith(s) for s in sources))
    assert not missed, f"roles add apt sources scripts/uninstall.sh never removes: {missed}"


def test_every_recorded_key_is_read_by_the_uninstaller() -> None:
    keys = set(re.findall(r"record_install (\w+) ", _INSTALL.read_text(encoding="utf-8")))
    assert {"created_user", "package", "apt_source"} <= keys, f"install.sh records only {sorted(keys)}"
    text = _UNINSTALL.read_text(encoding="utf-8")
    unread = sorted(k for k in keys if f"record_values {k}" not in text)
    assert not unread, f"install.sh records keys the uninstaller never reads: {unread}"


def test_the_log_lives_outside_everything_it_removes() -> None:
    log = re.search(r'^readonly LOG_FILE="([^"$]+)', _UNINSTALL.read_text(encoding="utf-8"), re.M).group(1)
    removed = [p for p in _array("PATHS") if not p.startswith("${")] + ["/opt/autobot"]
    assert not any(log.startswith(p.rstrip("/") + "/") for p in removed), f"{log} is inside a removed path"


def _plan_users(record: str | None, admin_gecos: str) -> list[str]:
    text = _UNINSTALL.read_text(encoding="utf-8")
    funcs = "\n".join(
        re.search(rf"^{n}\(\) \{{.*?^\}}", text, re.S | re.M).group(0) for n in ("record_values", "plan_users")
    )
    markers = re.search(r"^readonly ADMIN_MARKERS=.*$", text, re.M).group(0)
    with tempfile.TemporaryDirectory() as tmp:
        rec = Path(tmp) / "install-record"
        if record is not None:
            rec.write_text(record, encoding="utf-8")
        script = f"""set -euo pipefail
INSTALL_RECORD={rec}
ADMIN_ACCOUNT=autobot_admin
{markers}
id() {{ case "$1" in autobot|autobot_admin) return 0 ;; *) return 1 ;; esac; }}
getent() {{ echo "autobot_admin:x:1001:1001:{admin_gecos}:/home/autobot_admin:/bin/bash"; }}
{funcs}
plan_users
"""
        out = subprocess.run([shutil.which("bash") or "bash", "-c", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def test_no_account_is_removed_without_a_record_or_the_marker() -> None:
    """The defect: `autobot` existed (the owner's uid-1000 login) and would have been userdel'd."""
    assert _plan_users(None, "Martins") == []
    assert _plan_users("package=nodejs\n", "Someone Else") == []


def test_an_account_the_installer_created_is_removed() -> None:
    assert _plan_users("created_user=autobot\n", "Someone Else") == ["autobot"]


def test_autobots_admin_account_is_removed_by_its_marker() -> None:
    assert _plan_users(None, "Emergency Admin User (fallback access)") == ["autobot_admin"]
    assert _plan_users(None, "AutoBot Backup Admin (Emergency Access)") == ["autobot_admin"]
