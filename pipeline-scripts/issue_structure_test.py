# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Fixture tests for issue_structure.py (#19522)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import issue_structure as mod
import pytest

REPO = "mrveiss/AutoBot-AI"
API = "https://api.github.com"
GOAL = "M5 - Something new"


def issue(
    number: int = 10,
    milestone: Optional[str] = GOAL,
    labels: Tuple[str, ...] = ("phase:M5.1",),
    parent: Optional[int] = 1,
    subs: int = 0,
    body: str = "",
    state: str = "open",
) -> Dict[str, Any]:
    return {
        "id": 9000 + number,
        "number": number,
        "state": state,
        "body": body,
        "milestone": {"title": milestone} if milestone else None,
        "labels": [{"name": n} for n in labels],
        "parent_issue_url": f"{API}/repos/{REPO}/issues/{parent}" if parent else None,
        "sub_issues_summary": {"total": subs, "completed": 0, "percent_completed": 0},
    }


def bot_comment(cid: int, body: str) -> Dict[str, Any]:
    return {"id": cid, "body": body, "user": {"type": "Bot"}, "created_at": f"2026-10-25T00:00:{cid:02d}Z"}


class FakeApi:
    """Records every request; answers from the issue fixture and canned statuses."""

    def __init__(self, fixture: Dict[str, Any], link_status: int = 201, label_exists: bool = True) -> None:
        self.repository = REPO
        self.api_root = API
        self.token = "x"
        self.fixture = fixture
        self.link_status = link_status
        self.label_exists = label_exists
        self.calls: List[Tuple[str, str, Any]] = []

    def request(self, method: str, path: str, payload: Any = None) -> Tuple[int, Any]:
        self.calls.append((method, path, payload))
        if method == "GET" and path == f"/repos/{REPO}/issues/{self.fixture['number']}":
            return 200, self.fixture
        if method == "GET" and "/labels/" in path:
            return (200, {}) if self.label_exists else (404, {"message": "Not Found"})
        if path.endswith("/sub_issues"):
            return self.link_status, {"message": "already has a parent"} if self.link_status != 201 else {}
        if method == "POST" and path.endswith(f"/issues/{self.fixture['number']}/labels"):
            return 200, [{"name": mod.STRUCTURE_LABEL}]  # the real endpoint answers 200, not 201
        return {"POST": 201, "PATCH": 200, "DELETE": 200}.get(method, 200), {"id": 1}

    def writes(self) -> List[Tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] != "GET"]


def run(fixture: Dict[str, Any], comments: Optional[List[Dict[str, Any]]] = None, **kw: Any):
    api = FakeApi(fixture, **kw)
    client = mod.Client(api, comments=lambda _n: list(comments or []))
    return mod.run_check(client, fixture["number"]), api


# ---- parsing ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body,expected",
    [
        ("Parent: #42", 42),
        ("Parent #42", 42),
        ("intro\n- **Parent:** #42\n", 42),
        ("parent:#7", 7),
        ("the parent #42 is mentioned in prose", None),
        ("Part of #42", None),
        ("`Parent: #42`", None),
        ("<!-- Parent: #42 -->", None),
        ("```\nParent: #42\n```", None),
        ("Parent: #10\nParent: #11", 11),  # own number skipped
        ("", None),
    ],
)
def test_parse_parent_ref(body: str, expected: Optional[int]) -> None:
    assert mod.parse_parent_ref(body, own_number=10) == expected


def test_parent_action_never_reparents() -> None:
    assert mod.parent_action(None, None, REPO) == mod.ACTION_NONE
    assert mod.parent_action(5, None, REPO) == mod.ACTION_LINK
    assert mod.parent_action(5, f"{REPO.lower()}#5", REPO) == mod.ACTION_ALREADY
    assert mod.parent_action(5, f"{REPO.lower()}#6", REPO) == mod.ACTION_CONFLICT
    assert mod.parent_action(5, "other/repo#5", REPO) == mod.ACTION_CONFLICT


# ---- AC: parent linking is idempotent and never re-parents ----------------------------


def test_parent_ref_links_an_orphan_once() -> None:
    _, api = run(issue(parent=None, body="Parent: #42"))
    links = [c for c in api.writes() if c[1].endswith("/sub_issues")]
    assert links == [("POST", f"/repos/{REPO}/issues/42/sub_issues", {"sub_issue_id": 9010})]
    assert "replace_parent" not in links[0][2]


def test_parent_ref_already_linked_is_a_no_op() -> None:
    missing, api = run(issue(parent=42, body="Parent: #42"))
    assert missing == [] and api.writes() == []


def test_different_parent_is_never_replaced_and_commented_once() -> None:
    fixture = issue(parent=7, body="Parent: #42")
    _, api = run(fixture)
    assert not [c for c in api.calls if c[1].endswith("/sub_issues")]
    posts = [c for c in api.writes() if c[1].endswith("/comments")]
    assert len(posts) == 1 and mod.CONFLICT_MARKER in posts[0][2]["body"] and "#7" in posts[0][2]["body"]
    _, again = run(fixture, comments=[bot_comment(3, posts[0][2]["body"])])
    assert again.writes() == []


def test_failed_link_is_named_in_the_structure_comment() -> None:
    missing, api = run(issue(parent=None, body="Parent: #42"), link_status=422)
    assert len(missing) == 1 and "`Parent: #42` could not be linked (HTTP 422" in missing[0]
    assert any(c[1].endswith("/labels") and c[0] == "POST" for c in api.writes())


# ---- AC: needs-structure appears and clears; one comment, updated --------------------


def test_new_issue_without_milestone_phase_or_parent_is_flagged() -> None:
    missing, api = run(issue(milestone=None, labels=(), parent=None), label_exists=False)
    assert len(missing) == 3
    writes = api.writes()
    assert writes[0][1] == f"/repos/{REPO}/labels" and writes[0][2]["name"] == mod.STRUCTURE_LABEL
    assert writes[1] == ("POST", f"/repos/{REPO}/issues/10/labels", {"labels": [mod.STRUCTURE_LABEL]})
    body = writes[2][2]["body"]
    assert writes[2][1] == f"/repos/{REPO}/issues/10/comments" and body.startswith(mod.STRUCTURE_MARKER)
    assert "none is set" in body and "found none" in body and "native parent" in body


def test_flagged_issue_rerun_writes_nothing() -> None:
    fixture = issue(milestone=None, labels=(mod.STRUCTURE_LABEL,), parent=None)
    first, _ = run(fixture)
    _, api = run(fixture, comments=[bot_comment(4, mod.structure_comment(first))])
    assert api.writes() == []


def test_changed_gap_updates_the_comment_instead_of_posting() -> None:
    fixture = issue(labels=(mod.STRUCTURE_LABEL,), parent=None)
    _, api = run(fixture, comments=[bot_comment(4, mod.structure_comment(["old text"]))])
    assert [c[:2] for c in api.writes()] == [("PATCH", f"/repos/{REPO}/issues/comments/4")]


def test_label_clears_when_all_three_are_present() -> None:
    fixture = issue(labels=("phase:M5.1", mod.STRUCTURE_LABEL))
    missing, api = run(fixture, comments=[bot_comment(4, mod.structure_comment(["x"]))])
    assert missing == []
    assert api.writes()[0] == ("DELETE", f"/repos/{REPO}/issues/10/labels/{mod.STRUCTURE_LABEL}", None)
    assert api.writes()[1][:2] == ("PATCH", f"/repos/{REPO}/issues/comments/4")
    assert api.writes()[1][2]["body"] == mod.resolved_comment()


def test_complete_issue_never_flagged_gets_no_comment() -> None:
    assert run(issue())[1].writes() == []


def test_person_quoting_the_marker_does_not_capture_the_slot() -> None:
    human = {"id": 5, "body": mod.STRUCTURE_MARKER, "user": {"type": "User"}, "created_at": "z"}
    _, api = run(issue(parent=None, labels=("phase:M5.1", mod.STRUCTURE_LABEL)), comments=[human])
    assert [c[0] for c in api.writes()] == ["POST"]


# ---- AC: milestone and phase rules ----------------------------------------------------


def test_m5_milestone_with_phase_and_parent_is_accepted() -> None:
    facts = mod.facts_from_issue(issue(milestone=GOAL, labels=("phase:M5.1",)), f"{REPO}#1")
    assert mod.missing_items(facts) == []


def test_v0_milestone_counts_as_missing() -> None:
    missing = mod.missing_items(mod.facts_from_issue(issue(milestone="v0.9", labels=("phase:M1.2",)), "x#1"))
    assert missing[0] == "a goal milestone `M<n> - ...`: `v0.9` is not a goal milestone"
    assert "`phase:M1.2`" in missing[1] and len(missing) == 2


def test_phase_from_another_milestone_is_a_mismatch() -> None:
    fixture = issue(milestone="M3 - Third", labels=("phase:M2.1",))
    assert mod.missing_items(mod.facts_from_issue(fixture, "x#1")) == [
        "a `phase:M3.<k>` label matching milestone `M3 - Third`: found `phase:M2.1`"
    ]


def test_umbrella_with_children_needs_no_parent() -> None:
    assert mod.missing_items(mod.facts_from_issue(issue(parent=None, subs=2), None)) == []
    assert len(mod.missing_items(mod.facts_from_issue(issue(parent=None, subs=0), None))) == 1


@pytest.mark.parametrize("title", ["M10 - Ten", "M1 - Seamless install and consistent docs"])
def test_any_goal_milestone_number_counts(title: str) -> None:
    assert mod.goal_number(title) == int(title[1:].split(" ")[0])


@pytest.mark.parametrize("title", [None, "", "v0.9", "M1- x", "Milestone 1 - x", "m1 - x"])
def test_non_goal_milestones(title: Optional[str]) -> None:
    assert mod.goal_number(title) is None


# ---- skips and parent lookup ----------------------------------------------------------


def test_closed_issue_is_skipped() -> None:
    missing, api = run(issue(milestone=None, parent=None, state="closed"))
    assert missing == [] and api.writes() == []


def test_parent_endpoint_used_when_field_absent() -> None:
    fixture = issue()
    del fixture["parent_issue_url"]
    api = FakeApi(fixture)
    api.request = lambda m, p, payload=None: (  # type: ignore[method-assign]
        (200, {"url": f"{API}/repos/{REPO}/issues/77"}) if p.endswith("/parent") else (404, {})
    )
    assert mod.Client(api).parent_of(fixture) == f"{REPO.lower()}#77"


def test_parent_identity_rejects_unknown_url() -> None:
    with pytest.raises(mod.StructureError):
        mod.parent_identity("https://example.invalid/nope")
