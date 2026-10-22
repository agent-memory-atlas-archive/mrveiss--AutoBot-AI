# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""No compose port is published on every interface by accident, and no credential has a default (#17500, #17793).

Redis was published as ``${AUTOBOT_REDIS_PORT:-6379}:6379``, on every interface, with no
password, while Postgres and ChromaDB sat on 127.0.0.1. One copied line brings that back, so
every published port in every compose file is checked: it binds loopback, or it is a web entry
point listed in ``_ALL_INTERFACES`` with the reason it must face the network.

Credentials fell back to guessable values (``:-autobot``, ``:-admin``) when unset, so a stack
started without docker/generate-secrets.sh ran on passwords anyone could guess. Every
credential is now ``${VAR:?...}``: compose refuses to start and names the variable. Every such
variable is one docker/generate-secrets.sh writes, so the documented first run satisfies them.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from repo_tests._paths import repo_root

_ROOT = repo_root()
_GENERATOR = _ROOT / "docker" / "generate-secrets.sh"

#: (compose file, service, container port): why it must listen on every interface.
_ALL_INTERFACES = {
    ("docker-compose.yml", "autobot-frontend", "80"): "the web UI entry point; nginx serves users on the LAN",
    ("docker-compose.yml", "autobot-frontend", "443"): "the web UI entry point over TLS",
    ("docker-compose.yml", "autobot-slm-gui", "80"): "the SLM-only profile's web entry point",
    ("docker-compose.yml", "autobot-slm-gui", "443"): "the SLM-only profile's web entry point over TLS",
}
_LOOPBACK = {"127.0.0.1", "::1", "[::1]", "localhost"}
#: Credentials passed through EMPTY when unset (``${VAR:-}``): never a guessable value, and each
#: is generated, optional or fails closed where it is used. A new one needs a reason here.
_EMPTY_WHEN_UNSET = {
    "AUTOBOT_JWT_SECRET": "autobot-secrets-init generates it on first up (GH#9905); with-secrets.sh loads it",  # pragma: allowlist secret
    "SECRET_KEY": "autobot-secrets-init generates it on first up (GH#9905); with-secrets.sh loads it",  # pragma: allowlist secret
    "SLM_SECRET_KEY": "the SLM generates and persists it when unset (autobot-slm-backend/config.py)",  # pragma: allowlist secret
    "AUTOBOT_ENCRYPTION_KEY": "empty, EncryptionService raises where encryption is used -- it fails closed",  # pragma: allowlist secret
    "AUTOBOT_CHROMADB_AUTH_TOKEN": "optional ChromaDB token auth (#12513); ChromaDB binds loopback only",  # pragma: allowlist secret
    "ANTHROPIC_API_KEY": "an optional third-party provider key",  # pragma: allowlist secret
    "GH_TOKEN": "an optional third-party token",  # pragma: allowlist secret
}
_CREDENTIAL = re.compile(r"PASSWORD|SECRET|TOKEN|_KEY\b|PASSPHRASE", re.I)


def _compose_files() -> list[Path]:
    files = sorted({*_ROOT.glob("docker-compose*.yml"), *(_ROOT / "docker").glob("*.yml")})
    assert any(f.name == "docker-compose.yml" for f in files), "the compose files were not found"
    return files


def _host_ip_and_target(entry) -> tuple[str | None, str]:
    """The host address a port entry binds (None = every interface) and its container port."""
    if isinstance(entry, dict):  # long syntax
        return entry.get("host_ip"), str(entry.get("target"))
    text = str(entry).split("/")[0]
    # Split on ':' outside ${...} so "127.0.0.1:${PORT:-6379}:6379" parses as three parts.
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += ch == "{"
        depth -= ch == "}"
        if ch == ":" and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return (parts[0] if len(parts) == 3 else None), parts[-1]


def exposed_ports(compose: dict, name: str) -> list[tuple[str, str, str]]:
    """(file, service, container port) for each port published beyond loopback."""
    found = []
    for service, spec in (compose.get("services") or {}).items():
        for entry in (spec or {}).get("ports") or []:
            host_ip, target = _host_ip_and_target(entry)
            if host_ip not in _LOOPBACK:
                found.append((name, service, target))
    return found


def credential_defaults(text: str) -> list[str]:
    """Every ${VAR:-x} / ${VAR-x} with a NON-empty x whose VAR names a credential."""
    return [m.group(0) for m in re.finditer(r"\$\{([A-Z0-9_]+):?-([^}]+)\}", text) if _CREDENTIAL.search(m.group(1))]


def empty_passthroughs(text: str) -> set[str]:
    """Credentials written ${VAR:-} / ${VAR-}: passed through empty when unset."""
    return {m.group(1) for m in re.finditer(r"\$\{([A-Z0-9_]+):?-\}", text) if _CREDENTIAL.search(m.group(1))}


def required_credentials(text: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"\$\{([A-Z0-9_]+):\?[^}]*\}", text) if _CREDENTIAL.search(m.group(1))}


def test_no_port_is_published_on_every_interface_without_a_reason() -> None:
    exposed = [
        hit
        for f in _compose_files()
        for hit in exposed_ports(yaml.safe_load(f.read_text(encoding="utf-8")) or {}, f.name)
    ]
    unexplained = sorted(set(exposed) - set(_ALL_INTERFACES))
    assert not unexplained, f"published on every interface; bind 127.0.0.1 or list it with a reason: {unexplained}"
    stale = sorted(set(_ALL_INTERFACES) - set(exposed))
    assert not stale, f"allowances for ports no longer published that way: {stale}"


def test_redis_is_loopback_only() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    ports = compose["services"]["autobot-redis"]["ports"]
    assert all(_host_ip_and_target(p)[0] in _LOOPBACK for p in ports), ports


def test_no_compose_credential_has_a_default() -> None:
    hits = {f.name: credential_defaults(f.read_text(encoding="utf-8")) for f in _compose_files()}
    assert not any(hits.values()), f"a credential falls back to a default instead of refusing to start: {hits}"


def test_every_empty_credential_passthrough_has_a_reason() -> None:
    found = set().union(*(empty_passthroughs(f.read_text(encoding="utf-8")) for f in _compose_files()))
    assert not found - set(
        _EMPTY_WHEN_UNSET
    ), f"empty when unset, with no stated reason: {found - set(_EMPTY_WHEN_UNSET)}"
    assert (
        not set(_EMPTY_WHEN_UNSET) - found
    ), f"reasons for passthroughs that are gone: {set(_EMPTY_WHEN_UNSET) - found}"


def test_every_required_credential_is_one_generate_secrets_writes() -> None:
    required = set().union(*(required_credentials(f.read_text(encoding="utf-8")) for f in _compose_files()))
    generated = set(re.findall(r"^ensure_secret ([A-Z0-9_]+)", _GENERATOR.read_text(encoding="utf-8"), re.M))
    assert required, "no required credential found -- the detector is reading the wrong thing"
    assert (
        required <= generated
    ), f"compose requires credentials generate-secrets.sh never writes: {required - generated}"


def test_contrast_the_detectors_fire_on_the_old_shapes() -> None:
    old = {"services": {"autobot-redis": {"ports": ["${AUTOBOT_REDIS_PORT:-6379}:6379", "127.0.0.1:8001:8001"]}}}
    assert exposed_ports(old, "x.yml") == [("x.yml", "autobot-redis", "6379")]
    new = {"services": {"autobot-redis": {"ports": ["127.0.0.1:${AUTOBOT_REDIS_PORT:-6379}:6379"]}}}
    assert exposed_ports(new, "x.yml") == []
    long_form = {"services": {"s": {"ports": [{"target": 80, "published": 80}, {"target": 1, "host_ip": "127.0.0.1"}]}}}
    assert exposed_ports(long_form, "x.yml") == [("x.yml", "s", "80")]
    assert empty_passthroughs("J=${AUTOBOT_JWT_SECRET:-} K=${AUTOBOT_DB_PASSWORD:-autobot}") == {"AUTOBOT_JWT_SECRET"}
    assert credential_defaults(
        "A=${AUTOBOT_DB_PASSWORD:-autobot} B=${SLM_ADMIN_PASSWORD-admin} C=${LOG_LEVEL:-INFO} D=${GH_TOKEN:-}"
    ) == [
        "${AUTOBOT_DB_PASSWORD:-autobot}",
        "${SLM_ADMIN_PASSWORD-admin}",
    ]
    assert required_credentials("X=${AUTOBOT_DB_PASSWORD:?run it} Y=${AUTOBOT_DOCKER_MGMT_PREFIX:?run it}") == {
        "AUTOBOT_DB_PASSWORD"
    }


def credential_values(env_text: str) -> list[str]:
    """Keys of a tracked env file that set a credential to a non-empty value."""
    return [
        m.group(1)
        for m in re.finditer(r"^([A-Z0-9_]+)=(.+)$", env_text, re.M)
        if _CREDENTIAL.search(m.group(1)) and m.group(2).strip()
    ]


def test_the_tracked_docker_env_file_sets_no_credential() -> None:
    """A tracked value satisfies compose's ${VAR:?} as well as a generated one does (#17793)."""
    assert not credential_values((_ROOT / "docker" / ".env.docker").read_text(encoding="utf-8"))
    assert credential_values("AUTOBOT_DB_USER=autobot\nAUTOBOT_DB_PASSWORD=autobot\nGH_TOKEN=\n") == [
        "AUTOBOT_DB_PASSWORD"
    ]
