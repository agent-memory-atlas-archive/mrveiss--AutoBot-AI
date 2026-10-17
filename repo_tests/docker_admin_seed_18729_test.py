# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""A fresh Docker install has a main-app login (#18729).

The backend seeds its first platform admin from AUTOBOT_ADMIN_PASSWORD (#10199), and the
Docker path never set it: generate-secrets.sh wrote no such key and the backend's compose
`environment:` list did not pass one, so every fresh Docker stack started with no account
anyone could log in with.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml
from repo_tests._paths import repo_root

from tools.lint._comment_syntax import code_lines

_ROOT = repo_root()
_GENERATOR = _ROOT / "docker" / "generate-secrets.sh"


def _backend_env() -> dict[str, str]:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    entries = compose["services"]["autobot-backend"]["environment"]
    return dict(e.split("=", 1) for e in entries)


def _run_generator(tmp: Path) -> tuple[str, dict[str, str]]:
    bash = shutil.which("bash")
    if bash is None or shutil.which("openssl") is None:
        pytest.skip("bash and openssl are needed to run generate-secrets.sh")
    script = tmp / "generate-secrets.sh"
    shutil.copy(_GENERATOR, script)
    env = {**os.environ, "AUTOBOT_DOCKER_MGMT_PREFIX": "10.77.3"}
    out = subprocess.run([bash, str(script)], capture_output=True, text=True, timeout=60, env=env)
    assert out.returncode == 0, out.stderr
    lines = (tmp / ".env.secrets").read_text(encoding="utf-8").splitlines()
    return out.stdout, dict(line.split("=", 1) for line in lines if "=" in line)


def test_generate_secrets_writes_an_admin_password_the_user_policy_accepts() -> None:
    from autobot_shared.user_management.schemas.user import UserCreate

    with tempfile.TemporaryDirectory() as tmp:
        stdout, secrets = _run_generator(Path(tmp))
        password = secrets["AUTOBOT_ADMIN_PASSWORD"]
        assert UserCreate.validate_password(password) == password
        assert password not in stdout, "the admin password was printed"
        assert "AUTOBOT_ADMIN_PASSWORD" in stdout, "the operator is not told where the login is"
        _, again = _run_generator(Path(tmp))
        assert again["AUTOBOT_ADMIN_PASSWORD"] == password, "a re-run replaced the admin password"


def test_the_admin_password_reaches_the_backend_container() -> None:
    env = _backend_env()
    assert re.fullmatch(r"\$\{AUTOBOT_ADMIN_PASSWORD(:-)?\}", env.get("AUTOBOT_ADMIN_PASSWORD", "")), env.get(
        "AUTOBOT_ADMIN_PASSWORD"
    )


def test_the_compose_user_mode_default_is_a_mode_the_backend_accepts() -> None:
    source = (_ROOT / "autobot-backend" / "user_management" / "config.py").read_text(encoding="utf-8")
    enum = re.search(r"class DeploymentMode\(str, Enum\):(.*?)\n\n\n", source, re.S).group(1)
    modes = set(re.findall(r'=\s*"(\w+)"', enum))
    default = re.fullmatch(r"\$\{AUTOBOT_USER_MODE:-(\w+)\}", _backend_env()["AUTOBOT_USER_MODE"]).group(1)
    assert modes and default in modes, f"{default!r} is not one of {sorted(modes)} -- retired single_user? (#10636)"


def runnable_pulls(markdown: str) -> set[str]:
    """Models pulled by uncommented ``ollama pull`` lines inside fenced bash/sh blocks."""
    blocks = re.findall(r"^```(?:bash|sh|shell)\n(.*?)^```", markdown, re.S | re.M)
    return {
        m
        for b in blocks
        for line in code_lines(b, name="block.sh", strip_trailing=True)
        for m in re.findall(r"\bollama pull (\S+)", line.text)
    }


def test_runnable_pulls_contrast() -> None:
    live = "```bash\nollama pull a:1\n```\n"
    assert runnable_pulls(live) == {"a:1"}
    assert runnable_pulls("```bash\n# ollama pull a:1\n```\n") == set()
    assert runnable_pulls("prose: ollama pull a:1\n") == set()


def test_install_md_pulls_the_models_docker_env_names() -> None:
    names = dict(
        line.split("=", 1)
        for line in (_ROOT / "docker" / ".env.docker").read_text(encoding="utf-8").splitlines()
        if line.startswith(("AUTOBOT_DEFAULT_LLM_MODEL=", "AUTOBOT_EMBEDDING_MODEL="))
    )
    pulled = runnable_pulls((_ROOT / "INSTALL.md").read_text(encoding="utf-8"))
    assert len(names) == 2 and set(names.values()) <= pulled, (names, pulled)
