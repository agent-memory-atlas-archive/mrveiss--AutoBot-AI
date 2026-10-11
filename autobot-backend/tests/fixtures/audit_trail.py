# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""An in-memory canonical audit trail for SecurityLayer tests (#17225 D6).

Records what ``security_layer.emit_security`` is given and answers
``security_layer.query_filtered`` through the real projection reader, so a test
exercises the real filter and decode path without a Redis server.
"""

import json
from unittest.mock import patch

from services.audit.projection import read_events
from services.audit.taxonomy import AuditCategory, AuditEvent


class InMemoryAuditTrail:
    """The canonical audit trail, in memory: what SecurityLayer writes, it reads back (#17225 D6)."""

    def __init__(self) -> None:
        self.members: list[str] = []

    def emit_security(self, action, actor_id, *, resource_type=None, metadata=None, outcome="success", **_):
        event = AuditEvent(
            category=AuditCategory.SECURITY,
            action=action,
            actor_id=actor_id,
            resource_type=resource_type,
            metadata=metadata or {},
            outcome=outcome,
        )
        self.members.append(json.dumps(event.to_dict()))
        return True

    async def zrevrangebyscore(self, key, high, low, start=0, num=None):
        return list(reversed(self.members))[:num]

    async def query_filtered(self, flt, limit=100, offset=0):
        return (await read_events(self, flt, max_scan=10_000))[offset : offset + limit]

    def patched(self):
        """Route SecurityLayer's audit writes and reads through this trail."""
        return _Patchers(
            patch("security_layer.emit_security", side_effect=self.emit_security),
            patch("security_layer.query_filtered", side_effect=self.query_filtered),
        )


class _Patchers:
    def __init__(self, *patchers) -> None:
        self.patchers = patchers

    def start(self) -> None:
        for p in self.patchers:
            p.start()

    def stop(self) -> None:
        for p in self.patchers:
            p.stop()
