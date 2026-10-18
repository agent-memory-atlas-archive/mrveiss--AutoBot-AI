# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The issue-claims CLI's API transport: exit mapping, token handling, Redis default (#18811)."""

from __future__ import annotations

import json

import httpx
import pytest

from autobot_shared.coordination import issue_claims, issue_claims_api
from autobot_shared.egress_allowlist import reset_internal_origin_state

URL = "http://claims.test:8443"
TOKEN = "tok-SECRET-0123456789"
HOLDER = {"agent_id": "alice/s1", "scope": "issue:7"}


@pytest.fixture
def api(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n", encoding="utf-8")
    monkeypatch.setenv("AUTOBOT_ISSUE_CLAIMS_API_URL", URL)
    monkeypatch.setenv("AUTOBOT_ISSUE_CLAIMS_TOKEN_FILE", str(token_file))
    seen: list[httpx.Request] = []
    state = {"handler": lambda req: httpx.Response(200, json={"held": False})}

    def _client(token: str) -> httpx.AsyncClient:
        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return state["handler"](req)

        return httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(issue_claims_api, "_client", _client)
    yield state, seen
    reset_internal_origin_state()


@pytest.mark.parametrize(
    "response,code,field",
    [
        (httpx.Response(200, json={"held": True, "granted": True}), issue_claims.EXIT_OK, None),
        (httpx.Response(409, json={"detail": {"held": True, "granted": False, "holder": HOLDER}}), 1, None),
        (httpx.Response(400, json={"detail": "owner must not be empty"}), issue_claims.EXIT_USAGE, None),
        (httpx.Response(422, json={"detail": []}), issue_claims.EXIT_USAGE, None),
        (httpx.Response(401, json={"detail": "x"}), issue_claims.EXIT_UNREADABLE, "AuthRefused"),
        (httpx.Response(403, json={"detail": "x"}), issue_claims.EXIT_UNREADABLE, "AuthRefused"),
        (httpx.Response(500, text="boom"), issue_claims.EXIT_UNREADABLE, "HTTP500"),
        (httpx.Response(409, text="not json"), issue_claims.EXIT_UNREADABLE, "HTTP409"),
    ],
)
def test_status_maps_to_exit_code(api, response, code, field):
    state, _ = api
    state["handler"] = lambda req: response
    got, body = issue_claims.run_claims_command(["claim", "7", "--owner", "s1"])
    assert got == code
    if field:
        assert body["error_class"] == field and body["granted"] is False
    if got == 1:
        assert body["holder"] == HOLDER


def test_connection_error_fails_closed(api):
    state, _ = api

    def boom(req):
        raise httpx.ConnectError("refused")

    state["handler"] = boom
    code, body = issue_claims.run_claims_command(["who", "7"])
    assert code == issue_claims.EXIT_UNREADABLE and body["error_class"] == "ConnectError"


def test_each_subcommand_hits_its_endpoint(api):
    state, seen = api
    state["handler"] = lambda req: httpx.Response(200, json={"held": False, "claims": []})
    for argv in (
        ["claim", "7", "--owner", "s1", "--ttl-s", "120"],
        ["release", "7", "--owner", "s1"],
        ["who", "7"],
        ["list"],
    ):
        assert issue_claims.run_claims_command(argv)[0] == 0
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/api/coordination/issue-claims/7"),
        ("DELETE", "/api/coordination/issue-claims/7"),
        ("GET", "/api/coordination/issue-claims/7"),
        ("GET", "/api/coordination/issue-claims"),
    ]
    assert json.loads(seen[0].content) == {"owner": "s1", "note": "", "ttl_s": 120}
    assert seen[1].url.params["owner"] == "s1"


def test_token_comes_from_the_file_and_is_never_printed(api, capsys, monkeypatch, caplog):
    _, seen = api
    argv = ["who", "7"]
    assert issue_claims.main(argv) == 0
    assert seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in " ".join(argv) and TOKEN not in capsys.readouterr().out and TOKEN not in caplog.text


@pytest.mark.parametrize("setup", ["missing", "empty", "unset"])
def test_unusable_token_file_fails_closed_without_a_request(api, monkeypatch, tmp_path, setup):
    _, seen = api
    if setup == "missing":
        monkeypatch.setenv("AUTOBOT_ISSUE_CLAIMS_TOKEN_FILE", str(tmp_path / "nope"))
    elif setup == "empty":
        (tmp_path / "e").write_text("\n", encoding="utf-8")
        monkeypatch.setenv("AUTOBOT_ISSUE_CLAIMS_TOKEN_FILE", str(tmp_path / "e"))
    else:
        monkeypatch.delenv("AUTOBOT_ISSUE_CLAIMS_TOKEN_FILE")
    code, body = issue_claims.run_claims_command(["claim", "7", "--owner", "s1"])
    assert code == issue_claims.EXIT_UNREADABLE and body["granted"] is False and not seen


def test_bad_request_is_refused_before_any_request(api):
    _, seen = api
    assert issue_claims.run_claims_command(["claim", "0", "--owner", "s1"])[0] == issue_claims.EXIT_USAGE
    assert not seen


def test_metadata_address_url_is_refused_by_the_egress_policy(api, monkeypatch):
    _, seen = api
    monkeypatch.setenv("AUTOBOT_ISSUE_CLAIMS_API_URL", "http://169.254.169.254:80")
    code, _ = issue_claims.run_claims_command(["who", "7"])
    assert code == issue_claims.EXIT_UNREADABLE and not seen


def test_env_unset_uses_the_redis_path(monkeypatch):
    monkeypatch.delenv("AUTOBOT_ISSUE_CLAIMS_API_URL", raising=False)
    called = []

    async def fake_dispatch(args):
        called.append(args.cmd)
        return 0, {"held": False}

    monkeypatch.setattr(issue_claims, "_dispatch", fake_dispatch)
    assert issue_claims.run_claims_command(["who", "7"]) == (0, {"held": False})
    assert called == ["who"]
