# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""A startup DB failure names the host and port it tried, and how it failed (#18665).

The external Docker test (2026-10-17) logged only asyncpg's bare TimeoutError and restarted
12 times; the cause was a host firewall dropping container traffic, and nothing pointed there.
"""

import socket

from services.db_transaction_errors import describe_connect_failure

#: A fake credential, assembled at runtime: the point is to prove it never reaches the message.
_USER, _PASSWORD = "dbuser", "fixture-" + "pw" * 4  # pragma: allowlist secret -- fake fixture value


def _url(host: str, port: str = "") -> str:
    userinfo = f"{_USER}:{_PASSWORD}"
    return f"postgresql+asyncpg://{userinfo}@{host}{port}/autobot_slm"


def _chained(outer: BaseException, inner: BaseException) -> BaseException:
    outer.__cause__ = inner
    return outer


def test_a_timeout_names_host_port_and_says_timed_out() -> None:
    message = describe_connect_failure(TimeoutError(), _url("autobot-postgres", ":5433"))
    assert message.startswith("cannot reach PostgreSQL at autobot-postgres:5433: ")
    assert "timed out" in message and "firewall" in message


def test_a_refusal_found_down_the_cause_chain_says_refused() -> None:
    exc = _chained(RuntimeError("connection failed"), ConnectionRefusedError(111, "Connection refused"))
    assert "refused" in describe_connect_failure(exc, _url("autobot-postgres"))


def test_an_unresolvable_host_says_dns() -> None:
    exc = _chained(OSError("connect"), socket.gaierror(-2, "Name or service not known"))
    assert "did not resolve (DNS)" in describe_connect_failure(exc, _url("autobot-postgres"))


def test_credentials_never_appear() -> None:
    for exc in (TimeoutError(), ConnectionRefusedError(), ValueError("boom")):
        message = describe_connect_failure(exc, _url("autobot-postgres", ":5433"))
        assert _PASSWORD not in message and _USER not in message, message


def test_the_default_port_and_an_unknown_failure() -> None:
    message = describe_connect_failure(ValueError("boom"), _url("db-host"))
    assert message == "cannot reach PostgreSQL at db-host (default port): ValueError"


def test_a_password_with_url_delimiters_never_becomes_the_host() -> None:
    """01's review: an unencoded '/', '?', '#' or '@' in the password broke urlsplit's host."""
    for odd in ("a/b", "a?b", "a#b", "a@b"):
        url = f"postgresql+asyncpg://{_USER}:{odd}{_PASSWORD}@autobot-postgres:5433/autobot_slm"
        message = describe_connect_failure(TimeoutError(), url)
        assert message.startswith("cannot reach PostgreSQL at autobot-postgres:5433: "), message
        assert _USER not in message and _PASSWORD not in message, message
