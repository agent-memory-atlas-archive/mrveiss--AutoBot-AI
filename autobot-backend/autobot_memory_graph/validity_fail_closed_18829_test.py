# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Graph validity fails closed and compares instants, not strings (#18829).

``_is_entity_valid`` compared ``valid_to`` to ``now().isoformat()`` as a string, so
``"garbage"`` -- which sorts after every ISO date -- counted as valid, and a ``Z``
suffix sorted after ``.ffffff+00:00``. Each case here is paired with a valid control.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from autobot_memory_graph.queries import QueryOperationsMixin, _is_entity_valid, _is_entity_valid_at

_NOW = datetime.now(tz=timezone.utc)
_PAST = (_NOW - timedelta(hours=1)).isoformat()
_FUTURE = (_NOW + timedelta(hours=1)).isoformat()


def _entity(name: str, **metadata) -> Dict[str, Any]:
    return {"id": name, "type": "TASK", "name": name, "observations": [], "metadata": metadata}


class TestIsEntityValid:
    def test_a_garbage_valid_to_is_not_valid(self):
        assert _is_entity_valid(_entity("e", valid_to="garbage")) is False

    def test_a_past_valid_to_is_not_valid(self):
        assert _is_entity_valid(_entity("e", valid_to=_PAST)) is False

    def test_a_future_valid_to_is_valid(self):
        """Positive control."""
        assert _is_entity_valid(_entity("e", valid_to=_FUTURE)) is True

    def test_a_garbage_valid_from_is_not_valid(self):
        assert _is_entity_valid(_entity("e", valid_from="garbage")) is False


class TestIsEntityValidAt:
    def test_a_garbage_valid_to_is_not_valid(self):
        assert _is_entity_valid_at(_entity("e", valid_to="garbage"), _NOW.isoformat()) is False

    def test_a_garbage_valid_from_is_not_valid(self):
        assert _is_entity_valid_at(_entity("e", valid_from="garbage"), _NOW.isoformat()) is False

    def test_a_z_suffixed_end_before_as_of_is_expired(self):
        """String order puts ``...00Z`` after ``...00.500000+00:00``; the instants say otherwise."""
        entity = _entity("e", valid_to="2026-02-01T00:00:00Z")
        assert _is_entity_valid_at(entity, "2026-02-01T00:00:00.500000+00:00") is False

    def test_a_z_suffixed_end_after_as_of_is_valid(self):
        """Positive control."""
        entity = _entity("e", valid_to="2026-02-01T00:00:01Z")
        assert _is_entity_valid_at(entity, "2026-02-01T00:00:00.500000+00:00") is True


class _Graph(QueryOperationsMixin):
    """Only the query mixin, with Redis replaced by mocks."""

    def __init__(self, entities: List[Dict[str, Any]]) -> None:
        self.redis_client = MagicMock()
        self._execute_redis_search = AsyncMock(return_value=list(entities))
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=list(entities))
        self.redis_client.pipeline.return_value = pipe
        self._collect_entity_keys = AsyncMock(return_value=[f"memory:entity:{e['id']}" for e in entities])

    def ensure_initialized(self) -> None:
        """No-op: no real Redis."""


_ENTITIES = [
    _entity("garbage", valid_to="garbage"),
    _entity("past", valid_to=_PAST),
    _entity("future", valid_to=_FUTURE),
    _entity("open", valid_to=None),
]


@pytest.mark.asyncio
async def test_search_entities_serves_only_valid_entities():
    found = await _Graph(_ENTITIES).search_entities("*")
    assert [e["id"] for e in found] == ["future", "open"]


@pytest.mark.asyncio
async def test_the_fallback_search_serves_only_valid_entities():
    found = await _Graph(_ENTITIES)._fallback_search("*", None, 10)
    assert [e["id"] for e in found] == ["future", "open"]


@pytest.mark.asyncio
async def test_get_entities_as_of_serves_only_valid_entities():
    found = await _Graph(_ENTITIES).get_entities_as_of("TASK", _NOW.isoformat())
    assert [e["id"] for e in found] == ["future", "open"]
