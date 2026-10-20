# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Local-IP-toward-the-backend lookup shared by the worker's startup clients.

``config_bootstrap`` and ``backend_telemetry`` each carried a copy of this
function and a private cache (#18557). One home here: the packaged worker
cannot import ``autobot_shared`` (see ``worker_state.py``), so the shared core
lives in the worker's own ``utils`` package.

Issue #68: no hardcoded IPs -- the backend host is always a caller argument.
"""

import logging
import socket

logger = logging.getLogger(__name__)

LOCAL_IP_FALLBACK = "127.0.0.1"

_local_ip_cache: str | None = None  # Cache to avoid repeated socket calls


def get_local_ip(backend_host: str) -> str:
    """
    Get the local IP address that can reach the backend.

    Uses caching to avoid repeated socket operations (efficiency fix).

    Args:
        backend_host: Backend server hostname/IP

    Returns:
        Local IP address string
    """
    global _local_ip_cache

    if _local_ip_cache is not None:
        return _local_ip_cache

    sock = None
    try:
        # UDP connect only selects a route; no data is sent
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect((backend_host, 80))
        local_ip = sock.getsockname()[0]
        _local_ip_cache = local_ip
        return local_ip
    except Exception:
        return LOCAL_IP_FALLBACK
    finally:
        # Proper socket cleanup (Issue #68 - unclosed socket fix)
        if sock is not None:
            try:
                sock.close()
            except Exception:
                logger.debug("Suppressed exception in try block", exc_info=True)
