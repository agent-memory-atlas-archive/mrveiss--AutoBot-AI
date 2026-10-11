# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The OAuth bundle strip (#13630): dry run writes nothing, strip is gated, verified and value-free."""

import json
import uuid

import pytest

import api.admin_oauth_bundle_strip as proposer
from api.schemas_oauth_bundle_strip import OAuthBundleStripRequest
from knowledge.connectors import oauth_flow
from models.approval import Approval, ApprovalComment
from services import approval_execution, oauth_bundle_strip, oauth_bundle_strip_action
from services.approval_gate_service import ApprovalGateService

pytestmark = pytest.mark.asyncio

_SECRET = "app-client-secret-VALUE"  # pragma: allowlist secret
_LEGACY = {
    "access_token": "at-1",
    "refresh_token": "rt-1",
    "provider": "google",
    "token_url": "https://example.test/token",
    "scope": "s",
    "client_id": "app-id",
    "client_secret": _SECRET,
}


class _FakeSvc:
    def __init__(self, bundles: dict[str, str]):
        self.rows = dict(bundles)
        self.writes: list[str] = []
        self.corrupt_on_write = False

    def list_secrets(self, **_kw):
        return [{"id": sid} for sid in self.rows]

    def get_secret(self, secret_id=None, include_value=False, accessed_by=None, **_kw):
        value = self.rows.get(secret_id)
        return None if value is None else {"id": secret_id, "value": value, "created_by": "u1"}

    def update_secret(self, secret_id, value=None, updated_by=None, **_kw):
        self.writes.append(secret_id)
        if self.corrupt_on_write and "client_secret" not in value:
            value = json.dumps({**json.loads(value), "access_token": "tampered"})
        self.rows[secret_id] = value
        return True


@pytest.fixture
def configured(monkeypatch):
    """google is configured; microsoft is not."""
    real = oauth_flow.resolve_client_credentials

    def _resolve(provider):
        if provider.name == "google":
            return ("id", "secret")
        return real(provider)

    monkeypatch.setattr(oauth_flow, "resolve_client_credentials", _resolve)


def _svc(**extra):
    bundles = {
        "s-legacy": json.dumps(_LEGACY),
        "s-clean": json.dumps({k: v for k, v in _LEGACY.items() if "client" not in k}),
    }
    bundles.update(extra)
    return _FakeSvc(bundles)


async def test_preview_counts_without_writing_or_returning_values(configured):
    svc = _svc(**{"s-ms": json.dumps({**_LEGACY, "provider": "microsoft"}), "s-bad": "not json"})
    report = await oauth_bundle_strip.preview(svc, "admin")
    assert report.summary() == {
        "stripped": {"google": 1},
        "clean": {"google": 1},
        "skipped_unconfigured": {"microsoft": 1},
        "unreadable": {"unknown": 1},
    }
    assert svc.writes == []
    assert _SECRET not in json.dumps(report.summary()) + json.dumps(report.secret_ids)


async def test_strip_removes_only_app_credentials_and_preserves_tokens(configured):
    svc = _svc()
    before = oauth_bundle_strip.token_digest(_LEGACY)
    report = await oauth_bundle_strip.strip_all(svc, "approver")
    after = json.loads(svc.rows["s-legacy"])
    assert "client_id" not in after and "client_secret" not in after
    assert oauth_bundle_strip.token_digest(after) == before
    assert after["scope"] == "s"
    assert svc.writes == ["s-legacy"]  # the already-clean bundle is never rewritten
    assert report.count(oauth_bundle_strip.STRIPPED) == 1


async def test_unconfigured_provider_is_skipped_so_its_refresh_keeps_working(configured):
    svc = _FakeSvc({"s-ms": json.dumps({**_LEGACY, "provider": "microsoft"})})
    report = await oauth_bundle_strip.strip_all(svc, "approver")
    assert json.loads(svc.rows["s-ms"])["client_secret"] == _SECRET
    assert report.count(oauth_bundle_strip.SKIPPED_UNCONFIGURED) == 1 and svc.writes == []


async def test_digest_mismatch_restores_the_original(configured):
    svc = _svc()
    svc.corrupt_on_write = True
    original = svc.rows["s-legacy"]
    report = await oauth_bundle_strip.strip_all(svc, "approver")
    assert svc.rows["s-legacy"] == original
    assert report.count(oauth_bundle_strip.VERIFY_FAILED) == 1


class _FakeSession:
    def __init__(self):
        self.added, self.approval = [], None

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if isinstance(obj, Approval):
            self.approval = obj
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None

    async def execute(self, _stmt):
        approval = self.approval

        class _R:
            def scalar_one_or_none(self):
                return approval

        return _R()


async def _noop():
    return None


async def test_propose_then_approve_runs_the_strip_and_audits(configured, monkeypatch):
    svc = _svc()
    monkeypatch.setattr("services.secrets_service.get_secrets_service", lambda: svc)
    monkeypatch.setattr(ApprovalGateService, "_notify", lambda self, event, approval: _noop())
    audited = []

    async def _audit(op, **kw):
        audited.append((op, kw))

    monkeypatch.setattr(oauth_bundle_strip_action, "audit_log", _audit)
    oauth_bundle_strip_action.register_strip_action()
    session = _FakeSession()

    resp = await proposer.propose_oauth_bundle_strip(
        OAuthBundleStripRequest(backup_reference="snap-1"), current_user={"username": "agent"}, session=session
    )
    assert resp.status == "pending" and svc.writes == []  # a proposal executes nothing

    await ApprovalGateService(session).approve(
        session.approval.id, decided_by="human", author_type="human", comment=None
    )
    assert svc.writes == ["s-legacy"]
    comments = [o for o in session.added if isinstance(o, ApprovalComment)]
    assert any("1 stripped" in c.body for c in comments) and _SECRET not in " ".join(c.body for c in comments)
    assert audited and audited[-1][0] == "oauth_bundle.strip_app_credentials"
    assert _SECRET not in json.dumps(audited[-1][1], default=str)


async def test_execution_refuses_without_a_backup_reference(configured):
    approval = Approval(context={"action": oauth_bundle_strip_action.ACTION}, id=uuid.uuid4())
    with pytest.raises(ValueError, match="backup_reference"):
        await oauth_bundle_strip_action.execute_oauth_bundle_strip(approval, _FakeSession())


def test_action_is_registered_with_its_proposer():
    oauth_bundle_strip_action.register_strip_action()
    assert approval_execution.registered_action_proposers()[oauth_bundle_strip_action.ACTION] == (
        "api.admin_oauth_bundle_strip"
    )
