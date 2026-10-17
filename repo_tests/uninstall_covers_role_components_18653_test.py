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

**What these do not cover.** Names templated from facts or computed expressions resolve only
when a literal prefix is already covered; nothing here runs an uninstall on a real host -- a run on a host with a prior install is the end-to-end proof.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml
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


def _docs() -> list[tuple[Path, object]]:
    out = []
    for path in _role_files():
        try:
            out.append((path, yaml.safe_load(path.read_text(encoding="utf-8"))))
        except yaml.YAMLError:
            continue
    assert len(out) > 0.9 * len(_role_files()), "too many ansible files failed to parse -- the scan would miss them"
    return out


def _variables(docs) -> dict[str, str]:
    """Plain-string values from role defaults, group_vars and play-level vars."""
    values: dict[str, str] = {}

    def take(mapping) -> None:
        for key, value in (mapping or {}).items():
            if isinstance(value, str) and "{{" not in value:
                values.setdefault(key, value)

    for path, doc in docs:
        if path.match("*/defaults/main.yml") or "group_vars" in path.parts:
            take(doc if isinstance(doc, dict) else {})
        elif isinstance(doc, list):
            for play in doc:
                if isinstance(play, dict) and isinstance(play.get("vars"), dict):
                    take(play["vars"])
    return values


def _walk(node):
    """(dest, loop items) for every module call with a `dest:`."""
    if isinstance(node, dict):
        loop = node.get("loop") or node.get("with_items")
        for value in node.values():
            if isinstance(value, dict) and isinstance(value.get("dest"), str):
                yield value["dest"], loop if isinstance(loop, list) else None
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _resolve(dest: str, variables: dict[str, str]) -> str:
    dest = re.sub(
        r"\{\{\s*(\w+)\s*\|\s*default\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}", lambda m: variables.get(m[1], m[2]), dest
    )
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: variables.get(m[1], m[0]), dest)


def _deployed(prefixes: tuple[str, ...]) -> tuple[set[str], set[str]]:
    """Destinations under `prefixes` the roles write: (resolved, still templated)."""
    docs = _docs()
    variables = _variables(docs)
    resolved, open_ = set(), set()
    for _, doc in docs:
        for dest, loop in _walk(doc):
            dest = dest.strip()
            if not dest.startswith(prefixes):
                continue
            for item in loop if (loop and "{{ item }}" in dest) else [None]:
                name = _resolve(dest.replace("{{ item }}", str(item)) if item is not None else dest, variables)
                (open_ if "{{" in name else resolved).add(name)
    return resolved, open_


def _covered(path: str, exact: set[str], globs: list[str]) -> bool:
    if path in exact or any(fnmatch.fnmatch(path, g) for g in globs):
        return True
    head = path.split("{{", 1)[0]  # a templated tail after a literal prefix a glob already covers
    return "{{" in path and any(g.endswith("*") and head.startswith(g[:-1]) and len(head) >= len(g) - 1 for g in globs)


def test_every_deployed_systemd_unit_is_removed_or_kept() -> None:
    resolved, open_ = _deployed(("/etc/systemd/system/",))
    assert len(resolved) >= 25, f"read {len(resolved)} deployed units -- the scan is broken"
    unit_dir = "/etc/systemd/system/"
    exact = {unit_dir + u for u in _array("UNITS") + _array("KEPT_UNITS")} | set(_array("UNIT_DROPINS"))
    globs = [unit_dir + g for g in _array("UNIT_GLOBS")]
    missed = sorted(d for d in resolved | open_ if not _covered(d, exact, globs))
    assert not missed, f"roles deploy units scripts/uninstall.sh neither removes nor keeps: {missed}"


def test_every_deployed_sudoers_file_is_removed() -> None:
    resolved, open_ = _deployed(("/etc/sudoers.d/",))
    assert resolved, "no sudoers destinations read -- the scan is broken"
    exact = {"/etc/sudoers.d/" + s for s in _array("SUDOERS")} | {
        "/etc/sudoers.d/autobot"
    }  # install.sh's own, via the record
    missed = sorted(d for d in resolved | open_ if not _covered(d, exact, []))
    assert not missed, f"roles deploy sudoers files scripts/uninstall.sh never removes: {missed}"


def test_every_deployed_config_file_is_removed() -> None:
    prefixes = (
        "/etc/logrotate.d/",
        "/etc/rsyslog.d/",
        "/etc/dnsmasq.d/",
        "/etc/nginx/sites-available/",
        "/etc/nginx/sites-enabled/",
    )
    resolved, open_ = _deployed(prefixes)
    assert len(resolved) >= 8, f"read {len(resolved)} config destinations -- the scan is broken"
    exact = set(_array("PATHS")) | set(_array("POSTGRES_PATHS"))
    missed = sorted(d for d in resolved | open_ if not _covered(d, exact, _array("PATH_GLOBS")))
    assert not missed, f"roles deploy config files scripts/uninstall.sh never removes: {missed}"


def test_every_apt_source_a_role_adds_is_removed_or_kept() -> None:
    sources = _array("APT_SOURCES") + _array("KEPT_APT_SOURCES") + _array("POSTGRES_APT_SOURCES")
    added = set()
    for path in _role_files():
        added |= set(re.findall(r"""apt_repo_filename:\s*["']([a-z0-9-]+)["']""", path.read_text(encoding="utf-8")))
    assert added, "no apt_repo_filename read -- the scan is broken"
    missed = sorted(a for a in added if not any(a.startswith(s) for s in sources))
    assert not missed, f"roles add apt sources scripts/uninstall.sh neither removes nor keeps: {missed}"


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
