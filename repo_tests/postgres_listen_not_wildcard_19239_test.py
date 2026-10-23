# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""No Ansible play or role makes PostgreSQL listen on every interface (#19239).

`postgresql_listen_addresses: "*"` exposed 5432 on every interface of the SLM and the database
VM; pg_hba narrowed the clients, but nothing narrowed the sockets. The plays now bind loopback
plus the host's own fleet address.
"""

from __future__ import annotations

import re

from repo_tests._paths import repo_root

_ANSIBLE = repo_root() / "autobot-slm-backend" / "ansible"
_WILDCARD = re.compile(r"""^\s*postgresql_listen_addresses:\s*["']?\*["']?\s*(#.*)?$""", re.MULTILINE)
_FLEET_PLAYS = ("playbooks/deploy-slm-manager.yml", "playbooks/deploy-user-management-db.yml")


def _wildcards(text: str) -> list[str]:
    return [m.group(0).strip() for m in _WILDCARD.finditer(text)]


def test_the_detector_sees_a_wildcard_and_not_a_bound_list() -> None:
    assert _wildcards('    postgresql_listen_addresses: "*"\n') == ['postgresql_listen_addresses: "*"']
    assert _wildcards("postgresql_listen_addresses: '*'  # all\n")
    assert _wildcards('postgresql_listen_addresses: "localhost,{{ slm_host }}"\n') == []


def test_no_play_or_role_listens_on_every_interface() -> None:
    files = sorted(_ANSIBLE.rglob("*.yml"))
    assert len(files) > 50, f"only {len(files)} Ansible files found under {_ANSIBLE}"
    hits = [f"{f.relative_to(repo_root())}: {w}" for f in files for w in _wildcards(f.read_text(encoding="utf-8"))]
    assert hits == [], hits


def test_the_fleet_plays_bind_loopback_and_their_own_address() -> None:
    for rel in _FLEET_PLAYS:
        text = (_ANSIBLE / rel).read_text(encoding="utf-8")
        lines = re.findall(r"^\s*postgresql_listen_addresses:.*$", text, re.MULTILINE)
        assert lines, f"{rel} no longer sets postgresql_listen_addresses"
        assert all("localhost" in line for line in lines), f"{rel}: {lines}"
