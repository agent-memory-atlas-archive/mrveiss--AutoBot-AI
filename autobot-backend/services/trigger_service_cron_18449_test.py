# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""next_cron_run runs on the engine the shared validator checks (#18449)."""

from datetime import datetime, timezone

import pytest

from services import trigger_service
from services.trigger_service import next_cron_run

BASE = datetime(2026, 10, 12, 12, 0, 30, tzinfo=timezone.utc)  # a Monday


def test_trigger_service_uses_the_shared_validator():
    from autobot_shared import cron_expression

    assert trigger_service.validate_cron_expression is cron_expression.validate_cron_expression


def test_every_expression_the_validator_accepts_has_a_next_run():
    """The old split (loose validator, narrow parser) would register what it could not run."""
    for expr in ("* * * * MON", "0 0 * * 7", "0 12 * JAN *"):
        assert next_cron_run(expr, after=BASE) > BASE


def test_day_names_now_schedule():
    assert next_cron_run("0 9 * * MON", after=BASE) == datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)


def test_day_of_month_and_weekday_are_ored_as_in_standard_cron():
    """Behaviour difference: the old parser ANDed day-of-month with weekday (the next Monday
    that is also the 1st); standard cron, and croniter, OR them."""
    assert next_cron_run("0 0 1 * 1", after=BASE) == datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)


def test_six_field_cron_is_refused_with_the_contract_code():
    with pytest.raises(ValueError, match="schedule.invalid_cron"):
        next_cron_run("* * * * * *", after=BASE)


def test_impossible_date_raises_value_error():
    with pytest.raises(ValueError):
        next_cron_run("0 0 31 2 *", after=BASE)
