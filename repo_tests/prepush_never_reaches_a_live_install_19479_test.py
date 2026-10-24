# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
"""A test run never dials a live install unless it says it targets one (#19479).

``integration`` tests drive whatever service listens at the configured URL. On a machine that
also runs an install that is the user's live system, and some of them write to it
(``chat_knowledge_system_e2e_test.py`` posts ``add_to_kb`` decisions). A push from such a machine
ran one against the live backend: it timed out and blocked the push, and a faster backend would
have let it write. The probe those tests call requires an explicit opt-in on top of a listener;
the behaviour is tested in ``autobot_shared/live_service_probe_test.py``, the ordering here.
"""

from __future__ import annotations

from repo_tests._paths import repo_root

_PROBE = repo_root() / "autobot_shared" / "live_service_probe.py"


def test_the_probe_checks_the_opt_in_before_dialling() -> None:
    source = _PROBE.read_text(encoding="utf-8")
    body = source[source.index("def require_live_endpoint") :]
    assert body.index("env_flag(LIVE_TESTS_ENV)") < body.index("endpoint_is_listening(")
