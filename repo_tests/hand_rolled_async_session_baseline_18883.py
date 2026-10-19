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
    _B + "api/admin_orphan_storage_test.py::<module>::_FakeSession": ("MIGRATE to FakeAsyncSession in #18883 slice 2"),
    _B
    + "api/approval_gates_action_validation_test.py::<module>::_FakeSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B
    + "api/chat_shared_links_tracking_16861_test.py::<module>::_FakeDB": (
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B
    + "api/orphan_storage_approval_chain_test.py::<module>::_FakeSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B
    + "knowledge/fact_store_relocation_17545_test.py::<module>::_FakeSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B
    + "knowledge/source_liveness_sweep_isolation_17545_test.py::<module>::_FakeSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B
    + "llc/scheduler/stalled_run_sweep_test.py::<module>::_Session": ("MIGRATE to FakeAsyncSession in #18883 slice 2"),
    _B
    + "llc/scheduler/workspace_lease_sweep_test.py::<module>::_Session": (
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B
    + "llc/services/workspace_lease_16818_test.py::<module>::_Session": (
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B
    + "llc/tests/test_heartbeat_scheduler.py::test_handle_quota_exhausted_no_company_skips_pause::_Session": (
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B
    + "llc/tests/test_heartbeat_scheduler.py::test_handle_quota_exhausted_records_and_pauses::_Session": (
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B
    + "llc/tests/test_heartbeat_scheduler.py::test_skip_records_skipped_and_no_heartbeat_bump::_Session": (
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B
    + "llc/tests/test_status_comment_is_stored_16017.py::test_a_failed_commit_leaves_no_comment_behind::_FailingCommitSession": (  # noqa: E501
        "KEEP-EXCEPTION: proxy over a real aiosqlite session, not a stand-in for one"
    ),
    _B
    + "services/approval_execution_test.py::<module>::_FakeSession": ("MIGRATE to FakeAsyncSession in #18883 slice 2"),
    _B
    + "services/approval_execution_test.py::test_a_failure_to_record_the_trail_itself_never_raises::_BrokenSession": (  # noqa: E501
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B + "services/approval_record_test.py::<module>::_Session": ("MIGRATE to FakeAsyncSession in #18883 slice 2"),
    _B
    + "services/oauth_bundle_strip_test.py::<module>::_FakeSession": ("MIGRATE to FakeAsyncSession in #18883 slice 2"),
    _B
    + "services/orphan_storage_cleanup_action_test.py::<module>::_FakeSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B
    + "tests/knowledge/test_facts_redaction_16985.py::test_two_different_secrets_get_different_durable_content_hashes::_FakeSession": (  # noqa: E501
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
    ),
    _B
    + "utils/activity_tracker_16464_test.py::<module>::_CapturingSession": (
        "MIGRATE to FakeAsyncSession in #18883 slice 2"
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
    + "tests/api/test_component_resolve_job.py::test_run_component_resolve_job_happy_path::_TrackingSession": (  # noqa: E501
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
FROZEN_TOTAL = 39
