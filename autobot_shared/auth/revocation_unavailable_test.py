# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The shared "revocation check unavailable" answer (#18892)."""

from autobot_shared.auth import revocation_unavailable as mod


def test_it_is_a_503_with_a_stable_detail_and_retry_after():
    exc = mod.revocation_unavailable_error()
    assert exc.status_code == 503
    assert exc.detail == "revocation_check_unavailable"
    assert exc.headers == {"Retry-After": str(max(1, mod.REVOCATION_RETRY_AFTER_SECONDS))}


def test_retry_after_is_never_below_one_second(monkeypatch):
    monkeypatch.setattr(mod, "REVOCATION_RETRY_AFTER_SECONDS", 0)
    assert mod.revocation_unavailable_error().headers["Retry-After"] == "1"


def test_it_does_not_set_a_bearer_challenge():
    """A WWW-Authenticate challenge would invite a client to re-authenticate."""
    assert "WWW-Authenticate" not in mod.revocation_unavailable_error().headers
