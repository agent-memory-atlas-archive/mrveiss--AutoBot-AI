# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The TLS certificate directories have ONE default each (#19317).

- ``AUTOBOT_TLS_CERT_DIR``: ``env_registry_tls.DEFAULT_TLS_CERT_DIR``, the PKI tree on the CA host.
- ``AUTOBOT_TLS_REMOTE_CERT_DIR``: ``env_registry_tls.DEFAULT_TLS_REMOTE_CERT_DIR``, the flat dir a
  fleet node serves from.
- ``AUTOBOT_TLS_CA_CERT``: ``ssot_tls.TLSConfig.ca_cert``, derived from the first.

The registry module is the home because it must import without pydantic; ``ssot_tls`` reads it.

``AUTOBOT_TLS_CERT_DIR`` had ten defaults in two shapes, ``certs`` and ``/etc/autobot/certs``, because
two concepts shared one name. Python readers import the home. A file that cannot import it -- an
image without ``autobot_shared``, Ansible, shell, an env template -- is listed in :data:`PINNED`, and
its restated default must equal the home. A default site in any other file is a re-added copy.

**Boundary.** Tracked files with the suffixes in :data:`_SUFFIXES` outside ``docs/`` and Markdown. A
site is one of the five shapes in :data:`_SHAPES`: a literal ``getenv``/``environ.get`` fallback, a
literal pydantic ``default=`` beside the alias, an Ansible ``lookup('env', ...) | default(...)``, an
env-file assignment, or a shell/compose ``${NAME:-...}``. A path literal that never names the
variable (``/etc/autobot/certs/server-cert.pem`` in a role) is outside this guard: #19366 owns it.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import pytest
from repo_tests._paths import repo_root
from repo_tests._reach import declare

from autobot_shared import env_registry_tls, ssot_tls
from autobot_shared.env_registry import REGISTRY
from tools.lint._scan_helpers import EmptyEnumeration, tracked_paths

HOME: dict[str, str] = {
    "AUTOBOT_TLS_CERT_DIR": env_registry_tls.DEFAULT_TLS_CERT_DIR,
    "AUTOBOT_TLS_REMOTE_CERT_DIR": env_registry_tls.DEFAULT_TLS_REMOTE_CERT_DIR,
    "AUTOBOT_TLS_CA_CERT": ssot_tls.TLSConfig.model_fields["ca_cert"].default,
}

_DIR, _REMOTE, _CA = "AUTOBOT_TLS_CERT_DIR", "AUTOBOT_TLS_REMOTE_CERT_DIR", "AUTOBOT_TLS_CA_CERT"
_ANSIBLE = "autobot-slm-backend/ansible"
_SECURITY_SH = "autobot-infrastructure/shared/scripts/security"

#: path -> the variables it may restate, each because it cannot import the home. Shrink-only:
#: an entry whose file no longer restates the variable fails as stale.
PINNED: dict[str, frozenset[str]] = {
    ".env.example": frozenset({_DIR, _REMOTE, _CA}),  # template, copied to .env
    "autobot-npu-worker/resources/windows-npu-worker/app/npu_worker.py": frozenset({_DIR}),  # standalone
    "autobot-infrastructure/shared/docker/ai-stack/ai_api_server.py": frozenset({_REMOTE}),  # no autobot_shared
    "autobot-infrastructure/autobot-npu-worker/docker/npu_inference_server.py": frozenset({_REMOTE}),  # ditto
    f"{_ANSIBLE}/distribute-tls-certs.yml": frozenset({_DIR, _REMOTE}),
    f"{_ANSIBLE}/enable-tls.yml": frozenset({_REMOTE}),
    f"{_ANSIBLE}/health-check.yml": frozenset({_REMOTE}),
    f"{_ANSIBLE}/roles/redis/defaults/main.yml": frozenset({_DIR, _REMOTE}),
    f"{_SECURITY_SH}/generate-tls-certificates.sh": frozenset({_DIR}),
    f"{_SECURITY_SH}/renew-certificates.sh": frozenset({_DIR}),
}

_SUFFIXES = (".py", ".yml", ".yaml", ".j2", ".sh", ".env", ".example", ".docker", ".toml", ".ini", ".cfg", ".conf")
_THIS = "repo_tests/tls_cert_dir_one_home_19317_test.py"

#: ``{name}`` is the escaped variable; group ``v`` is the restated default.
_SHAPES = (
    r"""(?:getenv|environ\.get)\(\s*["']{name}["']\s*,\s*(["'])(?P<v>[^"'\n]*)\1""",
    r"""default\s*=\s*(["'])(?P<v>[^"'\n]*)\1\s*,\s*alias\s*=\s*["']{name}["']""",
    r"""lookup\(\s*['"]env['"]\s*,\s*['"]{name}['"]\s*\)\s*\|\s*default\(\s*(['"])(?P<v>[^'"\n]*)\1""",
    r"""(?m)^[ \t]*(?:export[ \t]+)?{name}=(["']?)(?P<v>[^"'\s#]*)\1""",
    r"""\$\{{{name}:?-(?P<v>[^}}\n]*)\}}""",
)
_PATTERNS = {name: [re.compile(shape.format(name=re.escape(name))) for shape in _SHAPES] for name in HOME}


def default_sites(text: str) -> list[tuple[str, str]]:
    """``(name, restated default)`` for every default site of a :data:`HOME` variable in *text*."""
    return [(name, m.group("v")) for name, pats in _PATTERNS.items() for pat in pats for m in pat.finditer(text)]


def violations(sites: dict[str, list[tuple[str, str]]], pinned: dict[str, frozenset[str]]) -> list[str]:
    """Re-added copies, drifted pinned copies and stale pins, as message lines."""
    out = []
    for path, found in sorted(sites.items()):
        for name, value in found:
            if name not in pinned.get(path, frozenset()):
                out.append(f"{path}: re-added copy of {name}={value!r}; import env_registry_tls instead")
            elif value != HOME[name]:
                out.append(f"{path}: {name} default {value!r} != home {HOME[name]!r}")
    for path, names in sorted(pinned.items()):
        for name in sorted(names - {n for n, _ in sites.get(path, [])}):
            out.append(f"{path}: pinned for {name} but restates no default -- delete the PINNED entry")
    return out


def _population(root: Path) -> list[str]:
    try:
        tracked = tracked_paths(root, ".", exclude=("docs", "*.md", _THIS))
    except EmptyEnumeration:
        return []
    return [rel for rel in tracked if rel.endswith(_SUFFIXES) or Path(rel).name.startswith(".env")]


def _reference_count(root: Path) -> int:
    """The same population counted independently: no git pathspecs, every filter in Python.

    ``_population`` lets git apply the excludes; filtering the unpatterned listing here keeps the
    denominator from failing in lockstep with that pathspec (RATCHET_BASELINES rule 3).
    """
    try:
        listed = tracked_paths(root)
    except EmptyEnumeration:
        return 0
    return sum(
        1
        for rel in listed
        if not rel.startswith("docs/")
        and not rel.endswith(".md")
        and rel != _THIS
        and (rel.endswith(_SUFFIXES) or Path(rel).name.startswith(".env"))
    )


REACH = declare(
    "tls-cert-dir-one-home-19317",
    discover=_population,
    min_fraction=0.98,
    reference=_reference_count,
    what="tracked code, config, template, env and shell files outside docs",
)


@functools.lru_cache(maxsize=1)
def _live_sites() -> dict[str, list[tuple[str, str]]]:
    root = repo_root()
    out = {}
    for rel in _population(root):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        if found := default_sites(text):
            out[rel] = found
    return out


def test_every_default_site_is_pinned_and_equals_the_home() -> None:
    assert not violations(_live_sites(), PINNED), "\n".join(violations(_live_sites(), PINNED))


def test_the_registry_and_the_settings_use_the_home() -> None:
    fields = ssot_tls.TLSConfig.model_fields
    assert (fields["cert_dir"].default, fields["remote_cert_dir"].default) == (HOME[_DIR], HOME[_REMOTE])
    assert {name: REGISTRY[name].default for name in (_DIR, _REMOTE)} == {_DIR: HOME[_DIR], _REMOTE: HOME[_REMOTE]}


def test_the_two_directories_are_different_concepts() -> None:
    assert HOME[_DIR] != HOME[_REMOTE], "one default for two layouts is the fork #19317 removed"
    assert HOME[_CA] == f"{HOME[_DIR]}/ca/ca-cert.pem"


@pytest.mark.parametrize(
    "copy",
    [
        'd = os.getenv("AUTOBOT_TLS_CERT_DIR", "/etc/autobot/certs")',
        'd = os.environ.get("AUTOBOT_TLS_CERT_DIR", "certs")',
        'cert_dir: str = Field(default="certs", alias="AUTOBOT_TLS_CERT_DIR")',
        "x: \"{{ lookup('env', 'AUTOBOT_TLS_REMOTE_CERT_DIR') | default('/etc/autobot/certs', true) }}\"",
        "AUTOBOT_TLS_CERT_DIR=./docker/certs",
        'CERT_DIR="${AUTOBOT_TLS_CERT_DIR:-certs}"',
    ],
)
def test_a_deliberate_copy_in_an_unpinned_file_is_flagged(copy: str) -> None:
    found = {"autobot-backend/new_reader.py": default_sites(copy)}
    assert found["autobot-backend/new_reader.py"], f"detector missed {copy!r}"
    assert any("re-added copy" in line for line in violations(found, {}))


def test_derived_reads_and_prose_are_not_sites() -> None:
    derived = 'd = os.getenv("AUTOBOT_TLS_CERT_DIR", DEFAULT_TLS_CERT_DIR)\n# AUTOBOT_TLS_CERT_DIR - the PKI tree\n'
    assert default_sites(derived) == []


def test_a_drifted_pinned_copy_and_a_stale_pin_are_flagged() -> None:
    pinned = {"a.yml": frozenset({_DIR}), "b.sh": frozenset({_DIR})}
    lines = violations({"a.yml": [(_DIR, "/etc/autobot/certs")]}, pinned)
    assert any("a.yml" in line and "!= home" in line for line in lines)
    assert any("b.sh" in line and "delete the PINNED entry" in line for line in lines)
