# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""User-facing docs name only models the installer provisions, one spelling each (#15219).

The local-Ollama guide was built around `llama3:8b`, which the installer never pulls, and wrote
`mistral:7b` where the provisioned tag is `mistral:7b-instruct` -- a user following it hit
"model not found". The provisioned set is docs/developer/TIERED_MODEL_ROUTING.md. A doc may
still name another model when that line says it is not provisioned.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404  # fixed argv, no shell

from repo_tests._paths import repo_root

from autobot_shared.paths import scrubbed_git_env

#: Tags that are wrong spellings of a provisioned model, or models the installer never pulls.
_WRONG = re.compile(
    r"(?<![-\w])llama3:8b\b|\bllama3\.1:8b\b|\bmistral:7b(?!-instruct)\b|\bmistral:latest\b"
    r"|\bphi-?2\b|\bphi-3\b|\bcodellama[:-]"
)
_LABEL = re.compile(r"not provision", re.IGNORECASE)
#: Skipped trees: history and research record what was true then, and this doc table cites code.
_SKIP = ("docs/archive/", "docs/archives/", "docs/research/", "docs/adr/", "changelog/")
_SKIP_FILES = {"docs/developer/MODEL_REVISION_PINNING.md", "docs/developer/CONFIG_MIGRATION_CHECKLIST.md"}


def _docs() -> list[str]:
    out = subprocess.run(  # nosec B603 B607  # fixed argv, no shell
        ["git", "ls-files", "*.md"], cwd=repo_root(), capture_output=True, text=True, check=True, env=scrubbed_git_env()
    ).stdout.split()
    return [
        p
        for p in out
        if (p.startswith("docs/") or p in {"README.md", "INSTALL.md"})
        and not p.startswith(_SKIP)
        and p not in _SKIP_FILES
    ]


def _offenders(texts: dict[str, str]) -> list[str]:
    return [
        f"{path}:{n}: {line.strip()[:100]}"
        for path, text in texts.items()
        for n, line in enumerate(text.splitlines(), 1)
        if _WRONG.search(line) and not _LABEL.search(line)
    ]


def test_docs_name_only_provisioned_model_tags():
    files = _docs()
    assert len(files) > 100, f"enumerated only {len(files)} docs"
    root = repo_root()
    offenders = _offenders({p: (root / p).read_text(encoding="utf-8", errors="replace") for p in files})
    assert not offenders, (
        "use a provisioned tag (docs/developer/TIERED_MODEL_ROUTING.md), or say on the line that the "
        "model is not provisioned (#15219):\n  " + "\n  ".join(offenders)
    )


def test_the_detector_tells_wrong_tags_from_provisioned_ones():
    texts = {
        "a.md": "pull llama3:8b\nuse mistral:7b now\nphi-3 is fast\n",
        "b.md": "dolphin-llama3:8b\nmistral:7b-instruct\nphi3:mini\nllama3:70b, not provisioned here\n",
    }
    assert [o.split(":", 2)[1] for o in _offenders(texts)] == ["1", "2", "3"]
