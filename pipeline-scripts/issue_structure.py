#!/usr/bin/env python3
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Issue structure guard: goal milestone, matching phase label, native parent (#19522).

Run by the ``structure`` job of `.github/workflows/issue-triage.yml`. Two subcommands:

``check --number N``  one issue, on every opened/edited/milestoned/demilestoned/labeled/
  unlabeled event (and on ``workflow_dispatch`` with an issue number).
``sweep``             every open issue carrying ``needs-structure``, on a schedule. Native
  parent and sub-issue changes emit no workflow event, so a label raised for a missing
  parent would otherwise never clear when the parent is added in the UI.

For an open issue (pull requests and closed issues are skipped):

1. ``Parent: #N`` (or ``Parent #N``) at the start of a body line links the issue as a native
   sub-issue of #N when it has NO parent. A different existing parent is never replaced: a
   single comment (marker ``<!-- issue-structure:parent-conflict -->``) names both instead.
   Already the parent: nothing happens. The link call never sends ``replace_parent``.
2. Structure: (a) a goal milestone ``M<n> - ...`` (any ``n``; ``v0.x`` milestones are history
   and do not count), (b) a ``phase:M<n>.<k>`` label whose ``<n>`` equals the milestone's,
   (c) a native parent, unless the issue has at least one sub-issue (an umbrella). Anything
   missing: ``needs-structure`` is added (created if absent) and ONE comment, marker
   ``<!-- issue-structure:needs-structure -->``, names exactly what is missing; later runs
   update that comment instead of posting again. All present: the label is removed and an
   existing comment is updated to say so.

Never sets milestones, phase labels or parents beyond the ``Parent:`` link, never closes an
issue and never edits a body. Stdlib only; HTTP goes through ``github_api.GitHubApi`` and
comment pages are read by the one comment reader, ``.github/actions/upsert-pr-comment``.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from github_api import build_api  # noqa: E402

logger = logging.getLogger(__name__)

STRUCTURE_LABEL = "needs-structure"
STRUCTURE_LABEL_COLOR = "fbca04"
STRUCTURE_LABEL_DESCRIPTION = "Missing goal milestone, matching phase label or native parent (#19522)"
STRUCTURE_MARKER = "<!-- issue-structure:needs-structure -->"
CONFLICT_MARKER = "<!-- issue-structure:parent-conflict -->"
SWEEP_PAGE_SIZE = 100
SWEEP_MAX_PAGES = 5

ACTION_NONE = "none"
ACTION_LINK = "link"
ACTION_ALREADY = "already"
ACTION_CONFLICT = "conflict"

_UPSERT_PATH = Path(__file__).resolve().parent.parent / ".github/actions/upsert-pr-comment/upsert_pr_comment.py"


def _load_upsert() -> Any:
    """The one comment reader and bot-marker rule (#12984), loaded by path: its dir has a hyphen."""
    spec = importlib.util.spec_from_file_location("_upsert_pr_comment_19522", _UPSERT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {_UPSERT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_UPSERT = _load_upsert()


class StructureError(RuntimeError):
    """The API answered with something this script cannot act on."""


# ---- pure: parsing ---------------------------------------------------------------------

GOAL_MILESTONE_RE = re.compile(r"^M(\d+) - ")
PHASE_LABEL_RE = re.compile(r"^phase:M(\d+)\.(\d+)$")
_PARENT_REF_RE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+)?[*_]{0,2}parent[*_]{0,2}[ \t]*:?[*_]{0,2}[ \t]*#(\d+)\b", re.I | re.M
)
_CODE_AND_COMMENTS_RE = re.compile(r"<!--.*?-->|```.*?```|~~~.*?~~~|`[^`\n]*`", re.S)
_ISSUE_URL_RE = re.compile(r"/repos/([^/]+/[^/]+)/issues/(\d+)/?$")


def parse_parent_ref(body: Optional[str], own_number: int) -> Optional[int]:
    """The issue number of the first ``Parent: #N`` / ``Parent #N`` line, outside code and comments."""
    text = _CODE_AND_COMMENTS_RE.sub(" ", body or "")
    for match in _PARENT_REF_RE.finditer(text):
        number = int(match.group(1))
        if number != own_number:
            return number
    return None


def parent_identity(parent_issue_url: Optional[str]) -> Optional[str]:
    """``owner/repo#N`` from an API issue URL, or None when there is no parent."""
    if not parent_issue_url:
        return None
    match = _ISSUE_URL_RE.search(parent_issue_url)
    if not match:
        raise StructureError(f"unrecognised parent_issue_url: {parent_issue_url!r}")
    return f"{match.group(1).lower()}#{match.group(2)}"


def display_ref(identity: str, repo: str) -> str:
    """``#N`` for this repository, ``owner/repo#N`` for another."""
    owner_repo, _, number = identity.partition("#")
    return f"#{number}" if owner_repo == repo.lower() else identity


def parent_action(ref: Optional[int], current: Optional[str], repo: str) -> str:
    """What ``Parent: #ref`` asks for, given the issue's current parent identity."""
    if ref is None:
        return ACTION_NONE
    if current is None:
        return ACTION_LINK
    return ACTION_ALREADY if current == f"{repo.lower()}#{ref}" else ACTION_CONFLICT


# ---- pure: structure -------------------------------------------------------------------


@dataclass(frozen=True)
class Facts:
    """What the structure rule reads from one issue."""

    milestone: Optional[str]
    labels: Tuple[str, ...]
    parent: Optional[str]
    sub_issues: int
    link_failure: str = ""


def facts_from_issue(issue: Dict[str, Any], parent: Optional[str], link_failure: str = "") -> Facts:
    milestone = (issue.get("milestone") or {}).get("title")
    labels = tuple(label.get("name", "") for label in issue.get("labels") or [])
    total = int(((issue.get("sub_issues_summary") or {}).get("total")) or 0)
    return Facts(milestone, labels, parent, total, link_failure)


def goal_number(milestone: Optional[str]) -> Optional[int]:
    match = GOAL_MILESTONE_RE.match(milestone or "")
    return int(match.group(1)) if match else None


def phase_labels(labels: Iterable[str]) -> List[Tuple[str, int]]:
    found = []
    for name in labels:
        match = PHASE_LABEL_RE.match(name)
        if match:
            found.append((name, int(match.group(1))))
    return found


def _found(phases: Sequence[Tuple[str, int]]) -> str:
    return ", ".join(f"`{name}`" for name, _ in phases) if phases else "none"


def _milestone_item(milestone: Optional[str]) -> Optional[str]:
    if goal_number(milestone) is not None:
        return None
    if not milestone:
        return "a goal milestone `M<n> - ...`: none is set"
    return f"a goal milestone `M<n> - ...`: `{milestone}` is not a goal milestone"


def _phase_item(milestone: Optional[str], labels: Sequence[str]) -> Optional[str]:
    goal = goal_number(milestone)
    phases = phase_labels(labels)
    if goal is None:
        return f"a `phase:M<n>.<k>` label whose `<n>` matches the goal milestone: found {_found(phases)}"
    if any(n == goal for _, n in phases):
        return None
    return f"a `phase:M{goal}.<k>` label matching milestone `{milestone}`: found {_found(phases)}"


def _parent_item(facts: Facts) -> Optional[str]:
    if facts.parent is not None or facts.sub_issues > 0:
        return None
    item = (
        "a native parent issue (add one, or write `Parent: #N` in the body), unless this is an umbrella with sub-issues"
    )
    return f"{item}; {facts.link_failure}" if facts.link_failure else item


def missing_items(facts: Facts) -> List[str]:
    """Every structure requirement this issue does not meet, in a fixed order."""
    items = (_milestone_item(facts.milestone), _phase_item(facts.milestone, facts.labels), _parent_item(facts))
    return [item for item in items if item]


# ---- pure: comments and plan -----------------------------------------------------------


def structure_comment(missing: Sequence[str]) -> str:
    lines = [STRUCTURE_MARKER, f"`{STRUCTURE_LABEL}`: this issue is missing:", ""]
    lines += [f"- {item}" for item in missing]
    lines += ["", "The label clears on the next issue event once all three are present (#19522)."]
    return "\n".join(lines)


def resolved_comment() -> str:
    return (
        f"{STRUCTURE_MARKER}\n`{STRUCTURE_LABEL}` cleared: goal milestone, matching phase label and "
        "native parent (or sub-issues) are all present (#19522)."
    )


def conflict_comment(ref: int, current: str, repo: str) -> str:
    return (
        f"{CONFLICT_MARKER}\nThe body names `Parent: #{ref}`, but this issue's native parent is already "
        f"{display_ref(current, repo)}. It was not re-parented; fix the body or move the issue by hand (#19522)."
    )


def ours(comments: Iterable[Dict[str, Any]], marker: str) -> Optional[Dict[str, Any]]:
    """Our newest bot comment carrying ``marker``, or None."""
    mine = [c for c in comments if _UPSERT.is_ours(c, marker)]
    return max(mine, key=lambda c: (c.get("created_at") or "", c["id"])) if mine else None


def plan_structure(missing: Sequence[str], has_label: bool, existing: Optional[Dict[str, Any]]) -> List[Tuple]:
    """The writes that bring label and comment in line with ``missing``; empty when already in line."""
    actions: List[Tuple] = []
    if missing:
        if not has_label:
            actions.append(("add_label",))
        body = structure_comment(missing)
    else:
        if has_label:
            actions.append(("remove_label",))
        if existing is None:
            return actions
        body = resolved_comment()
    if existing is None:
        actions.append(("create_comment", body))
    elif existing.get("body") != body:
        actions.append(("update_comment", existing["id"], body))
    return actions


# ---- IO --------------------------------------------------------------------------------


class Client:
    """`GitHubApi.request` plus the one comment reader; tests pass a fake ``api``/``comments``."""

    def __init__(self, api: Any, comments: Optional[Callable[[int], List[Dict[str, Any]]]] = None) -> None:
        self.api = api
        self.repo = api.repository
        self._comments = comments

    def call(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, ok=(200,)) -> Any:
        status, body = self.api.request(method, path, payload)
        if status not in ok:
            raise StructureError(f"{method} {path}: HTTP {status}: {body}")
        return body

    def comments(self, number: int) -> List[Dict[str, Any]]:
        if self._comments is not None:
            return self._comments(number)
        return list(_UPSERT.iter_comments(self.api.api_root, self.repo, number, self.api.token))

    def issue(self, number: int) -> Dict[str, Any]:
        return self.call("GET", f"/repos/{self.repo}/issues/{number}")

    def parent_of(self, issue: Dict[str, Any]) -> Optional[str]:
        """The native parent's identity; ``parent_issue_url`` when present, else the parent endpoint."""
        if "parent_issue_url" in issue:
            return parent_identity(issue["parent_issue_url"])
        status, body = self.api.request("GET", f"/repos/{self.repo}/issues/{issue['number']}/parent", None)
        if status == 404:
            return None
        if status != 200 or not isinstance(body, dict):
            raise StructureError(f"cannot read the parent of #{issue['number']}: HTTP {status}: {body}")
        return parent_identity(body.get("url"))


def ensure_label(client: Client) -> None:
    name = urllib.parse.quote(STRUCTURE_LABEL)
    status, _ = client.api.request("GET", f"/repos/{client.repo}/labels/{name}", None)
    if status == 404:
        payload = {"name": STRUCTURE_LABEL, "color": STRUCTURE_LABEL_COLOR, "description": STRUCTURE_LABEL_DESCRIPTION}
        client.call("POST", f"/repos/{client.repo}/labels", payload, ok=(201, 422))
    elif status != 200:
        raise StructureError(f"cannot read label {STRUCTURE_LABEL}: HTTP {status}")


def _write_comment(client: Client, number: int, action: Tuple) -> None:
    if action[0] == "create_comment":
        client.call("POST", f"/repos/{client.repo}/issues/{number}/comments", {"body": action[1]}, ok=(201,))
    else:
        client.call("PATCH", f"/repos/{client.repo}/issues/comments/{action[1]}", {"body": action[2]})


def apply_plan(client: Client, number: int, actions: Sequence[Tuple]) -> None:
    for action in actions:
        if action[0] == "add_label":
            ensure_label(client)
            client.call("POST", f"/repos/{client.repo}/issues/{number}/labels", {"labels": [STRUCTURE_LABEL]})
        elif action[0] == "remove_label":
            path = f"/repos/{client.repo}/issues/{number}/labels/{urllib.parse.quote(STRUCTURE_LABEL)}"
            client.call("DELETE", path, ok=(200, 404))
        else:
            _write_comment(client, number, action)


def link_parent(client: Client, issue: Dict[str, Any], ref: int) -> Tuple[Optional[str], str]:
    """Add the issue as a sub-issue of #ref. Returns (new parent identity, failure note)."""
    path = f"/repos/{client.repo}/issues/{ref}/sub_issues"
    status, body = client.api.request("POST", path, {"sub_issue_id": issue["id"]})
    if status == 201:
        logger.info("#%s linked as a sub-issue of #%s", issue["number"], ref)
        return f"{client.repo.lower()}#{ref}", ""
    message = body.get("message", "") if isinstance(body, dict) else body
    logger.warning("linking #%s under #%s failed: HTTP %s %s", issue["number"], ref, status, message)
    return None, f"`Parent: #{ref}` could not be linked (HTTP {status}: {message})"


def _conflict(client: Client, number: int, ref: int, current: str, comments: List[Dict[str, Any]]) -> None:
    body = conflict_comment(ref, current, client.repo)
    existing = ours(comments, CONFLICT_MARKER)
    if existing is None:
        _write_comment(client, number, ("create_comment", body))
    elif existing.get("body") != body:
        _write_comment(client, number, ("update_comment", existing["id"], body))


def reconcile_parent(
    client: Client, issue: Dict[str, Any], comments: List[Dict[str, Any]]
) -> Tuple[Optional[str], str]:
    """Apply ``Parent: #N``; returns (parent identity after the step, link failure note)."""
    current = client.parent_of(issue)
    ref = parse_parent_ref(issue.get("body"), issue["number"])
    action = parent_action(ref, current, client.repo)
    logger.info("#%s parent: current=%s ref=%s action=%s", issue["number"], current, ref, action)
    if action == ACTION_LINK:
        return link_parent(client, issue, ref)
    if action == ACTION_CONFLICT:
        _conflict(client, issue["number"], ref, current, comments)
    return current, ""


def run_check(client: Client, number: int) -> List[str]:
    """Reconcile one issue. Returns the missing items (empty when the structure is complete)."""
    issue = client.issue(number)
    if issue.get("pull_request") or issue.get("state") != "open":
        logger.info("#%s skipped (pull request or not open)", number)
        return []
    comments = client.comments(number)
    parent, failure = reconcile_parent(client, issue, comments)
    facts = facts_from_issue(issue, parent, failure)
    missing = missing_items(facts)
    has_label = STRUCTURE_LABEL in facts.labels
    actions = plan_structure(missing, has_label, ours(comments, STRUCTURE_MARKER))
    apply_plan(client, number, actions)
    logger.info("#%s missing=%s actions=%s", number, missing, [a[0] for a in actions])
    return missing


def labelled_issues(client: Client) -> List[int]:
    """Open issues (not PRs) carrying the structure label, up to the sweep cap."""
    numbers: List[int] = []
    for page in range(1, SWEEP_MAX_PAGES + 1):
        query = urllib.parse.urlencode(
            {"state": "open", "labels": STRUCTURE_LABEL, "per_page": SWEEP_PAGE_SIZE, "page": page}
        )
        batch = client.call("GET", f"/repos/{client.repo}/issues?{query}")
        numbers += [item["number"] for item in batch if not item.get("pull_request")]
        if len(batch) < SWEEP_PAGE_SIZE:
            return numbers
    logger.warning("sweep stopped at the %d-page cap; the rest is checked on the next run", SWEEP_MAX_PAGES)
    return numbers


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check").add_argument("--number", type=int, required=True)
    sub.add_parser("sweep")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = Client(build_api())
    numbers = [args.number] if args.command == "check" else labelled_issues(client)
    failed = []
    for number in numbers:
        try:
            run_check(client, number)
        except StructureError as exc:
            logger.error("#%s: %s", number, exc)
            failed.append(number)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
