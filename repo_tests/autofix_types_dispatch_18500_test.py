# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""The generated-types autofix can be dispatched on a branch (#18500).

A merge train learned that its combined tree needs an ``api.ts`` patch only by
running the full CI suite. The job's steps now live once in a reusable
workflow, called on ``pull_request`` with the PR head and by a dispatch-only
workflow with the dispatched ref.

Why two callers, not a second trigger on one workflow: CodeQL read a PR-head
checkout in any dispatch-triggerable workflow as untrusted code with
default-branch cache access (``actions/cache-poisoning/poisonable-step``, five
alerts on train 23), whatever step-level ``if:`` guarded it -- its control-check
model never reads ``github.event_name``. These tests pin that no dispatch path
can reach a ``head_ref`` checkout.

**What these do not cover.** No run is dispatched and CodeQL is not run here;
the PR's own CodeQL check and the first train dispatch are the end-to-end proof.
"""

from __future__ import annotations

import yaml
from repo_tests._paths import repo_root

_DIR = repo_root() / ".github" / "workflows"
_REUSABLE_PATH = ".github/workflows/_generated-types-autofix.yml"
_REUSABLE = f"./{_REUSABLE_PATH}"  # how a caller names a local reusable workflow


def _doc(name: str) -> dict:
    doc = yaml.safe_load((_DIR / name).read_text(encoding="utf-8"))
    doc["on"] = doc.pop(True, doc.get("on"))  # PyYAML reads the bare key `on` as True
    return doc


def test_dispatch_runs_the_shared_job_on_the_dispatched_ref() -> None:
    doc = _doc("dispatch-generated-types.yml")
    assert set(doc["on"]) == {"workflow_dispatch"}
    job = doc["jobs"]["autofix-types"]
    assert job["uses"] == _REUSABLE and job["with"]["ref"] == "${{ github.ref }}"
    assert (
        doc["concurrency"]["group"] == "${{ github.workflow }}-${{ github.ref }}"
    ), "dispatches on different branches must not cancel each other"


def test_the_pull_request_caller_cannot_be_dispatched() -> None:
    doc = _doc("auto-fix-generated-types.yml")
    assert "workflow_dispatch" not in doc["on"], "a PR-head checkout must never sit in a dispatch-triggerable workflow"
    job = doc["jobs"]["autofix-types"]
    assert job["uses"] == _REUSABLE and job["with"]["ref"] == "${{ github.head_ref }}"


def test_the_shared_job_checks_out_only_its_input() -> None:
    steps = _doc("_generated-types-autofix.yml")["jobs"]["autofix-types"]["steps"]
    checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 1 and checkouts[0]["with"]["ref"] == "${{ inputs.ref }}"
    assert "head_ref" not in (_DIR / "_generated-types-autofix.yml").read_text(encoding="utf-8").split("jobs:", 1)[1]


def test_the_parked_run_approval_stays_pull_request_only() -> None:
    gate = _doc("auto-fix-generated-types.yml")["jobs"]["approve-parked-runs"]["if"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in gate


def test_the_patch_artifact_is_uploaded_whatever_the_caller() -> None:
    steps = _doc("_generated-types-autofix.yml")["jobs"]["autofix-types"]["steps"]
    upload = next(s for s in steps if s.get("with", {}).get("name") == "generated-types-patch")
    assert str(upload.get("if", "")).strip() == "failure()"
