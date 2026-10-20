# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Frozen allowlist of the hand-rolled fake async sessions that exist today (#18883).

Each key is `<repo path>::<enclosing function>::<class>` (`<module>` at module level); the value
is why it is still hand-rolled. The list may only SHRINK: a new match fails the guard, and an
entry whose class was migrated to `testkit.fake_async_session.FakeAsyncSession` (or left the
tree) fails it too until the line is deleted. `FROZEN_TOTAL` caps the length.
"""

from __future__ import annotations

_B = "autobot-backend/"
_S = "autobot-slm-backend/"

ALLOWLIST: dict[str, str] = {
    _B
    + "llc/tests/test_status_comment_is_stored_16017.py::test_a_failed_commit_leaves_no_comment_behind::_FailingCommitSession": (  # noqa: E501
        "KEEP-EXCEPTION: proxy over a real aiosqlite session, not a stand-in for one"
    ),
    _S
    + "services/reconciler_playbook_timeout_14524_test.py::<module>::_FakeSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "services/reconciler_remediation_tracker_expiry_14465_test.py::<module>::_FailingFirstCommitSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "services/reconciler_remediation_tracker_expiry_14465_test.py::<module>::_FailingResultCommitSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "services/reconciler_remediation_tracker_expiry_14465_test.py::<module>::_FakeSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "services/reconciler_remediation_tracker_expiry_14465_test.py::<module>::_HeartbeatSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "services/reconciler_service_remediation_persistence_16712_test.py::<module>::_CapturingSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "services/reconciler_service_remediation_persistence_16712_test.py::<module>::_RaceSimulatingSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "services/reconciler_status_restart_delta_14465_test.py::<module>::_SeededServiceHeartbeatSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "tests/api/test_component_resolve_job.py::_make_db_service_mock::_FakeSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "tests/api/test_component_resolve_job.py::_make_reconcile_db_mock::_FakeSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "tests/api/test_component_resolve_job.py::test_run_component_resolve_job_happy_path::_TrackingSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "tests/api/test_drift_resolve_advances_node_version_11512.py::_make_db_service_mock::_FakeSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "tests/api/test_fleet_failure_diagnostics_18714.py::<module>::_FakeDb": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "tests/api/test_llm_config_round_trip_17826.py::<module>::_Db": ("MIGRATE to FakeAsyncSession in #18883 slice 3"),
    _S
    + "tests/api/test_orchestration_fleet_actions_delegate_18714.py::<module>::_FakeDb": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "tests/test_backup_delete_retention_13307.py::<module>::_FakeDB": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S
    + "tests/test_resume_plan_carries_stage_logs_15881.py::<module>::_FakeSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 3"
    ),
    _S + "tests/test_secrets_backup_18281.py::<module>::_DB": ("MIGRATE to FakeAsyncSession in #18883 slice 3"),
    _S
    + "tests/test_secrets_backup_18281.py::<module>::_RetentionDB": ("MIGRATE to FakeAsyncSession in #18883 slice 3"),
}

#: The ceiling. Lower it with the list; never raise it.
FROZEN_TOTAL = 20
