# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Tell a connection-level DB failure apart from a row-level one (#17070).

A row-level failure (IntegrityError, a bad value hitting a constraint) is
safely absorbed by a SAVEPOINT rollback -- the connection is fine, only that
one statement's effects are undone. A connection-level failure (a lost or
timed-out connection) can't be recovered that way: a caller must stop
issuing statements on the session and abort the whole operation rather
than continue against a connection that's already gone.

`describe_connect_failure` says WHERE a connection failed and HOW (#18665): asyncpg's
bare `TimeoutError` named neither, so a firewall dropping container traffic looked like
any other startup crash.
"""

import errno
import socket

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError


def is_connection_level_db_error(exc: BaseException) -> bool:
    """True when *exc* leaves the connection itself unusable, not just one statement."""
    if isinstance(exc, (TimeoutError, OperationalError, InterfaceError)):
        return True
    return isinstance(exc, DBAPIError) and getattr(exc, "connection_invalidated", False)


def _causes(exc: BaseException) -> list[BaseException]:
    """*exc* and everything it was raised from, outermost first."""
    seen: list[BaseException] = []
    while exc is not None and exc not in seen:
        seen.append(exc)
        exc = exc.__cause__ or exc.__context__
    return seen


def _failure_kind(exc: BaseException) -> str:
    for cause in _causes(exc):
        if isinstance(cause, socket.gaierror):
            return "the host name did not resolve (DNS)"
        if isinstance(cause, ConnectionRefusedError) or getattr(cause, "errno", None) == errno.ECONNREFUSED:
            return "the connection was refused (nothing listening there, or the wrong port)"
        if isinstance(cause, TimeoutError):
            return "the connection timed out (no answer: a firewall dropping the traffic, or the host is down)"
    return f"{type(exc).__name__}"


def describe_connect_failure(exc: BaseException, database_url: str) -> str:
    """'cannot reach PostgreSQL at host:port: <timeout | refusal | DNS | type>', never credentials."""
    return f"cannot reach PostgreSQL at {_location(database_url)}: {_failure_kind(exc)}"


def _location(database_url: str) -> str:
    """host:port from a DSN, read only from the text after the LAST '@', so a password holding an
    unencoded '/', '?', '#' or '@' can never be reported as the host (#18672 review)."""
    rest = database_url.split("://", 1)[-1].rsplit("@", 1)[-1]
    hostport = rest.split("/", 1)[0].split("?", 1)[0]
    host, sep, port = hostport.rpartition(":") if hostport.count(":") == 1 else (hostport, "", "")
    if not sep:
        return f"{hostport or '?'} (default port)"
    return f"{host or '?'}:{port}" if port.isdigit() else f"{host or '?'}:?"
