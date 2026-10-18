# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The issue-claims CLI's API transport (#18811, parent #17098).

A developer checkout has no registry credential (its ``.env`` is the placeholder copy), so
``python -m autobot_shared.coordination.issue_claims`` can reach the registry through AutoBot's
``/api/coordination/issue-claims`` instead. It is used when ``AUTOBOT_ISSUE_CLAIMS_API_URL`` is set.

* The base URL is exactly that value -- never derived from the SSOT ``backend_url`` (#18810).
* The bearer token is read from the file named by ``AUTOBOT_ISSUE_CLAIMS_TOKEN_FILE``; it is never
  in argv, never taken from an environment *value*, and never printed or logged.
* The URL is operator-configured, so it is registered as an internal origin and still goes through
  the egress policy (a malformed or metadata-address URL is refused); redirects are not followed.
* Exit codes are the Redis transport's: 200 -> 0, 409 -> 1 (held, body names the holder), 400/422 -> 4,
  everything else -- 401/403 (``error_class`` ``AuthRefused``), 5xx, transport failure, an unreadable
  token file -- -> 3. Nothing is granted on a failure (fail closed).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import httpx

from autobot_shared.coordination.issue_claims import EXIT_HELD, EXIT_OK, EXIT_UNREADABLE, EXIT_USAGE
from autobot_shared.egress_allowlist import register_internal_origin_provider
from autobot_shared.env_utils import env_int_clamped, env_str
from autobot_shared.http_egress_guard import assert_egress_policy
from autobot_shared.logging_manager import get_logger

logger = get_logger(__name__)

API_URL_ENV = "AUTOBOT_ISSUE_CLAIMS_API_URL"
BEARER_FILE_ENV = "AUTOBOT_ISSUE_CLAIMS_TOKEN_FILE"
API_PATH = "/api/coordination/issue-claims"


def api_base_url() -> str:
    """The configured API base URL, or ``""`` when the Redis transport applies."""
    return env_str(API_URL_ENV, "").strip().rstrip("/")


async def _configured_origin() -> list[str]:
    return [api_base_url()]


def _timeout_s() -> int:
    return env_int_clamped("AUTOBOT_ISSUE_CLAIMS_API_TIMEOUT_S", 10, min_v=1, max_v=120)


def _read_token() -> str:
    """The bearer token from the file named by ``BEARER_FILE_ENV``; raises ``OSError`` / ``ValueError``."""
    path = env_str(BEARER_FILE_ENV, "").strip()
    if not path:
        raise ValueError(f"{BEARER_FILE_ENV} is not set")
    token = Path(path).read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("token file is empty")
    return token


def _client(token: str) -> httpx.AsyncClient:
    """The HTTP client; tests replace this to supply a mock transport."""
    return httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=_timeout_s(), follow_redirects=False)


def _request_for(args: argparse.Namespace) -> tuple[str, str, dict[str, Any]]:
    """``(method, path, httpx kwargs)`` for the parsed subcommand."""
    if args.cmd == "list":
        return "GET", API_PATH, {}
    path = f"{API_PATH}/{args.number}"
    if args.cmd == "who":
        return "GET", path, {}
    if args.cmd == "release":
        return "DELETE", path, {"params": {"owner": args.owner}}
    body = {"owner": args.owner, "note": args.note}
    if args.ttl_s is not None:
        body["ttl_s"] = args.ttl_s
    return "POST", path, {"json": body}


def _detail(response: httpx.Response) -> Any:
    try:
        return response.json().get("detail")
    except (ValueError, AttributeError):
        return None


def map_response(response: httpx.Response) -> tuple[int, dict]:
    """Map an API answer onto the CLI's exit code and JSON body."""
    status = response.status_code
    if status == 200:
        return EXIT_OK, response.json()
    if status == 409 and isinstance(_detail(response), dict):
        return EXIT_HELD, _detail(response)
    if status in (400, 422):
        return EXIT_USAGE, {"error": "bad-request", "message": str(_detail(response)), "granted": False}
    error_class = "AuthRefused" if status in (401, 403) else f"HTTP{status}"
    return EXIT_UNREADABLE, {"error": "registry-unreadable", "error_class": error_class, "granted": False}


async def run_api_command(args: argparse.Namespace) -> tuple[int, dict]:
    """Send *args*' subcommand to the API; a failure to get any answer is ``EXIT_UNREADABLE``."""
    method, path, kwargs = _request_for(args)
    try:
        token = _read_token()
        register_internal_origin_provider(_configured_origin)
        url = f"{api_base_url()}{path}"
        await assert_egress_policy(url)
        async with _client(token) as client:
            response = await client.request(method, url, **kwargs)
        return map_response(response)
    except Exception as exc:  # noqa: BLE001 -- every failure to get an answer fails closed
        logger.error("issue claims API unreachable (%s); refusing, nothing granted", type(exc).__name__)
        return EXIT_UNREADABLE, {"error": "registry-unreadable", "error_class": type(exc).__name__, "granted": False}
