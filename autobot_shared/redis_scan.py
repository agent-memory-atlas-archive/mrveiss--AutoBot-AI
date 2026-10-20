# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The one non-blocking pattern walk over a Redis keyspace (#18999).

``KEYS pattern`` blocks the server for the whole keyspace; ``scan_iter`` without COUNT
makes ~keys/10 round trips. Callers that need "every key matching a pattern" use
``scan_keys`` / ``count_keys`` here: same keys as ``KEYS``, bounded work per call
(COUNT from ``AUTOBOT_REDIS_SCAN_COUNT``, default 1000).
"""

import os
from typing import Any, List

REDIS_SCAN_COUNT = int(os.getenv("AUTOBOT_REDIS_SCAN_COUNT", "1000"))


async def scan_keys(client: Any, pattern: str) -> List[Any]:
    """Return every key matching ``pattern`` using SCAN with a large COUNT."""
    return [key async for key in client.scan_iter(match=pattern, count=REDIS_SCAN_COUNT)]


async def count_keys(client: Any, pattern: str) -> int:
    """Count keys matching ``pattern`` without materialising them."""
    total = 0
    async for _ in client.scan_iter(match=pattern, count=REDIS_SCAN_COUNT):
        total += 1
    return total
