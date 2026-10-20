# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The fact store's `active_only` read is the one serving predicate (#18820).

An inactive or expired stored fact is dropped; an active one is kept.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from models.atomic_fact import AtomicFact, FactType, TemporalType
from services.fact_extraction_service import FactExtractionService

_NOW = datetime.now(tz=timezone.utc)


def _stored(**overrides) -> str:
    fields = dict(
        subject="s",
        predicate="is",
        object="x",
        fact_type=FactType.FACT,
        temporal_type=TemporalType.DYNAMIC,
        confidence=0.9,
        source="test",
        extraction_method="manual",
        valid_from=_NOW - timedelta(days=1),
    )
    fields.update(overrides)
    return json.dumps(AtomicFact(**fields).to_dict())


def _read(data: str, active_only: bool = True):
    service = object.__new__(FactExtractionService)  # the filter reads no instance state
    return service._filter_and_deserialize_fact("f1", data, active_only, None)


def test_an_active_stored_fact_is_kept() -> None:
    assert _read(_stored()) is not None


def test_an_inactive_stored_fact_is_dropped() -> None:
    assert _read(_stored(is_active=False)) is None


def test_an_expired_stored_fact_is_dropped() -> None:
    assert _read(_stored(valid_until=_NOW - timedelta(hours=1))) is None


def test_active_only_false_returns_an_inactive_fact() -> None:
    """Contrast: the predicate applies only when `active_only` asks for it."""
    assert _read(_stored(is_active=False), active_only=False) is not None
