# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""The SLM admin password never appears on a process command line, and survives any characters (#19236).

It was on three argvs, readable by every local user through `ps` or /proc/<pid>/cmdline:
`ansible-playbook -e slm_admin_password=...`, the login `curl -d {...}`, and install.sh's own
`--admin-pass=`. It was also interpolated unquoted, so a space or quote changed the value
seeded or sent. Now Ansible reads it from an owner-only vars file (`-e @file`), curl reads the
login body on stdin (`--data @-`), both bodies are JSON-encoded by jq from the environment
(`env.PW`, not `--arg`, which would put it on jq's argv), and `--admin-pass=` is refused in
favour of `--admin-pass-file=`.

The tests run install.sh's own lines with stub `ansible-playbook` and `curl` that record argv
and input.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from repo_tests._paths import repo_root

_INSTALL = repo_root() / "install.sh"
#: Spaces, both quote kinds, a backslash, JSON and shell metacharacters.
_TRICKY = "p a\"ss' w\\rd {$x} ;&"


def _require_tools() -> str:
    bash = shutil.which("bash")
    if bash is None or shutil.which("jq") is None:
        pytest.skip("bash and jq are needed")
    return bash


def _stub(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / name).write_text("#!/bin/bash\n" + body, encoding="utf-8")
    (bin_dir / name).chmod(0o755)


def _run(bash: str, script: str, tmp: Path) -> subprocess.CompletedProcess:
    env = {"PATH": f"{tmp / 'bin'}:/usr/bin:/bin", "TMPDIR": str(tmp), "LOG": str(tmp / "log")}
    return subprocess.run([bash, "-c", script], capture_output=True, text=True, timeout=60, env=env)


def test_ansible_gets_the_password_from_an_owner_only_file_not_argv(tmp_path: Path) -> None:
    bash = _require_tools()
    text = _INSTALL.read_text(encoding="utf-8")
    m = re.search(r"    local vars_file;.*?_ansible_exit=\$\{PIPESTATUS\[0\]\}\n", text, re.S)
    assert m, "install.sh no longer builds the ansible vars file before ansible-playbook"
    _stub(
        tmp_path / "bin",
        "ansible-playbook",
        'printf "%s\\n" "$@" > "$LOG.argv"\n'
        'for a in "$@"; do case "$a" in @*) f="${a#@}"; cp "$f" "$LOG.vars"; stat -c %a "$f" > "$LOG.mode";; esac; done\n'
        'echo "PLAY RECAP"\n',  # the installer filters ansible output through grep, which needs a line
    )
    script = "\n".join(
        [
            "set -euo pipefail",
            f"ADMIN_PASSWORD={shlex.quote(_TRICKY)}",
            'LOG_FILE="$LOG.out" inventory=inv',
            "run() {",
            m.group(0),
            "}",
            "run",
            'echo "TEMP_LEFT=$(ls "$TMPDIR" | grep -c "^tmp\\." || true)"',
        ]
    )
    out = _run(bash, script, tmp_path)
    assert out.returncode == 0, out.stderr
    argv = (tmp_path / "log.argv").read_text(encoding="utf-8")
    assert _TRICKY not in argv and "slm_admin_password" not in argv, argv
    assert json.loads((tmp_path / "log.vars").read_text(encoding="utf-8")) == {"slm_admin_password": _TRICKY}
    assert (tmp_path / "log.mode").read_text(encoding="utf-8").strip() == "600"
    assert "TEMP_LEFT=0" in out.stdout, "the vars file holding the password was left behind"


def test_the_login_body_goes_on_stdin_json_encoded(tmp_path: Path) -> None:
    bash = _require_tools()
    text = _INSTALL.read_text(encoding="utf-8")
    m = re.search(r"    token=\$\(PW=.*?\|\| true[^\n]*\n", text, re.S)
    assert m, "install.sh no longer builds the login request this way"
    _stub(
        tmp_path / "bin",
        "curl",
        'printf "%s\\n" "$@" > "$LOG.argv"; cat > "$LOG.body"; echo \'{"access_token":"tok"}\'\n',
    )
    script = f'set -euo pipefail\nADMIN_PASSWORD={shlex.quote(_TRICKY)}\napi_url=https://h\n{m.group(0)}echo "TOKEN=$token"\n'
    out = _run(bash, script, tmp_path)
    assert out.returncode == 0, out.stderr
    assert "TOKEN=tok" in out.stdout
    assert _TRICKY not in (tmp_path / "log.argv").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "log.body").read_text(encoding="utf-8")) == {"username": "admin", "password": _TRICKY}


def test_no_external_command_line_carries_the_password() -> None:
    """Every remaining ${ADMIN_PASSWORD} use is an assignment, an env prefix, a file write or a
    shell builtin -- never an argument to curl, ansible-playbook, jq --arg or the like."""
    offenders = [
        line.strip()
        for line in _INSTALL.read_text(encoding="utf-8").splitlines()
        if "${ADMIN_PASSWORD}" in line
        and re.search(r"\b(curl|ansible-playbook|ansible|wget|jq\s+.*--arg|sshpass|mysql|psql)\b", line)
        and not re.search(r'PW="\$\{ADMIN_PASSWORD\}"', line)
    ]
    assert not offenders, offenders


def test_the_argv_flag_is_refused_and_a_file_flag_exists() -> None:
    text = _INSTALL.read_text(encoding="utf-8")
    assert re.search(r"--admin-pass=\*\)\s+fatal ", text), "--admin-pass= still sets the password from argv"
    assert "--admin-pass-file=*)" in text
