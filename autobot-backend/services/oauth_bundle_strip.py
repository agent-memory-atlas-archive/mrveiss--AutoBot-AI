# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Strip the legacy copy of the OAuth app credentials from stored user bundles (#13630).

Before #13630 every user's ``connector_oauth_token`` secret carried a copy of the
operator's OAuth ``client_id`` / ``client_secret``. Refresh now resolves them from
provider config, so the copy is a secret at rest in a second place.

Removing it rewrites stored credentials, which no agent may do on its own
(#17038): this module only provides the read-only **preview** and the **strip**
that ``oauth_bundle_strip_action`` runs after a human approves a proposal.
Nothing here is reachable from a route that executes it.

Safety properties of the strip, per bundle:
  * only ``client_id`` / ``client_secret`` are removed; every other field is kept;
  * a bundle whose provider has no operator config is SKIPPED -- stripping it would
    break its refresh (the fallback copy is all it has);
  * a SHA-256 of ``{access_token, refresh_token, provider, token_url}`` is compared
    before and after, and the original value is restored on any mismatch;
  * no secret value, and no provider name outside the known registry, is returned
    or logged -- results are ids and counts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field

from autobot_shared.logging_manager import get_logger
from autobot_shared.status_enums import SecretType
from services.credential_write import mirror_credential_to_vault

logger = get_logger(__name__)

APP_CREDENTIAL_KEYS = ("client_id", "client_secret")
#: The fields whose hash must be identical before and after the strip.
TOKEN_FIELDS = ("access_token", "refresh_token", "provider", "token_url")
UNKNOWN_PROVIDER = "unknown"

# Outcomes of one bundle.
STRIPPED, CLEAN, SKIPPED_UNCONFIGURED, UNREADABLE, VERIFY_FAILED = (
    "stripped",
    "clean",
    "skipped_unconfigured",
    "unreadable",
    "verify_failed",
)


@dataclass
class StripReport:
    """Ids and counts only. ``by_outcome`` maps outcome -> {provider -> count}."""

    by_outcome: dict[str, dict[str, int]] = field(default_factory=dict)
    secret_ids: dict[str, list[str]] = field(default_factory=dict)

    def add(self, outcome: str, provider: str, secret_id: str) -> None:
        counts = self.by_outcome.setdefault(outcome, {})
        counts[provider] = counts.get(provider, 0) + 1
        self.secret_ids.setdefault(outcome, []).append(secret_id)

    def count(self, outcome: str) -> int:
        return sum(self.by_outcome.get(outcome, {}).values())

    def summary(self) -> dict[str, dict[str, int]]:
        return {outcome: dict(counts) for outcome, counts in self.by_outcome.items()}


def token_digest(creds: dict) -> str:
    """SHA-256 over the token-bearing fields, so before/after can be compared without values."""
    material = json.dumps({k: creds.get(k) for k in TOKEN_FIELDS}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _provider_label(creds: dict) -> str:
    """The provider name only when it is a registered one; anything else is not echoed."""
    from knowledge.connectors import oauth_flow

    name = creds.get("provider")
    return name if name in oauth_flow.OAUTH_PROVIDERS else UNKNOWN_PROVIDER


def _provider_configured(creds: dict) -> bool:
    """Whether the operator's OAuth app for this bundle's provider resolves from config."""
    from knowledge.connectors import oauth_flow

    try:
        oauth_flow.resolve_client_credentials(oauth_flow.get_provider(creds.get("provider") or ""))
    except (KeyError, ValueError):
        return False
    return True


def _read_bundle(svc, secret_id: str, accessed_by: str) -> tuple[dict | None, dict | None]:
    """(secret row, parsed bundle); (None, None) when unreadable, expired or not JSON."""
    secret = svc.get_secret(secret_id=secret_id, include_value=True, accessed_by=accessed_by)
    if secret is None:
        return None, None
    try:
        creds = json.loads(secret["value"])
    except (TypeError, ValueError):
        return secret, None
    return secret, creds if isinstance(creds, dict) else None


def _list_bundle_ids(svc) -> list[str]:
    rows = svc.list_secrets(secret_type=SecretType.CONNECTOR_OAUTH_TOKEN.value)
    return [row["id"] for row in rows]


def _classify(svc, secret_id: str, accessed_by: str) -> tuple[str, str, dict | None, dict | None]:
    """(outcome-if-not-strippable | '', provider label, secret row, bundle)."""
    secret, creds = _read_bundle(svc, secret_id, accessed_by)
    if creds is None:
        return UNREADABLE, UNKNOWN_PROVIDER, secret, None
    label = _provider_label(creds)
    if not any(k in creds for k in APP_CREDENTIAL_KEYS):
        return CLEAN, label, secret, creds
    if not _provider_configured(creds):
        return SKIPPED_UNCONFIGURED, label, secret, creds
    return "", label, secret, creds


def preview_sync(svc, accessed_by: str) -> StripReport:
    """Dry run: what a strip WOULD do. ``stripped`` here means 'would be stripped'. Writes nothing."""
    report = StripReport()
    for secret_id in _list_bundle_ids(svc):
        outcome, label, _, _ = _classify(svc, secret_id, accessed_by)
        report.add(outcome or STRIPPED, label, secret_id)
    return report


def _write_verified(svc, secret_id: str, original: str, creds: dict, actor: str) -> bool:
    """Write the stripped bundle, verify the token digest, restore the original on mismatch."""
    before = token_digest(creds)
    stripped = {k: v for k, v in creds.items() if k not in APP_CREDENTIAL_KEYS}
    svc.update_secret(secret_id=secret_id, value=json.dumps(stripped, ensure_ascii=False), updated_by=actor)
    _, after = _read_bundle(svc, secret_id, actor)
    if after is not None and token_digest(after) == before and not any(k in after for k in APP_CREDENTIAL_KEYS):
        return True
    svc.update_secret(secret_id=secret_id, value=original, updated_by=actor)
    return False


def strip_one_sync(svc, secret_id: str, actor: str, report: StripReport) -> str | None:
    """Strip one bundle; returns the new value to mirror to the vault, or None."""
    outcome, label, secret, creds = _classify(svc, secret_id, actor)
    if outcome:
        report.add(outcome, label, secret_id)
        return None
    original = secret["value"]
    if not _write_verified(svc, secret_id, original, creds, actor):
        logger.error("OAuth bundle strip verification failed; original restored (secret %s)", secret_id)
        report.add(VERIFY_FAILED, label, secret_id)
        return None
    report.add(STRIPPED, label, secret_id)
    return json.dumps({k: v for k, v in creds.items() if k not in APP_CREDENTIAL_KEYS}, ensure_ascii=False)


async def preview(svc, accessed_by: str) -> StripReport:
    return await asyncio.get_running_loop().run_in_executor(None, preview_sync, svc, accessed_by)


async def strip_all(svc, actor: str) -> StripReport:
    """Strip every legacy bundle whose provider is configured. Run ONLY after human approval."""
    from knowledge.connectors.credential_store import _vault_write_enabled

    loop = asyncio.get_running_loop()
    report = StripReport()
    for secret_id in await loop.run_in_executor(None, _list_bundle_ids, svc):
        new_value = await loop.run_in_executor(None, strip_one_sync, svc, secret_id, actor, report)
        if new_value is not None and _vault_write_enabled():
            secret = await loop.run_in_executor(None, svc.get_secret, secret_id)
            await mirror_credential_to_vault(secret_id, (secret or {}).get("created_by") or "", new_value)
    return report
