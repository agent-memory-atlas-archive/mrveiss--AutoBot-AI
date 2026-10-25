# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""edit_file fails loudly on a miss, counts occurrences, keeps CRLF (#19463).

Each test drives the real ``POST /mcp/edit_file`` endpoint over a synthetic
allowed directory, so the assertions cover what the model actually sees and
what actually lands on disk.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.filesystem_mcp as fs_mcp
from api.filesystem_edit import detect_line_ending, find_closest_span

_SOURCE = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """An allowed directory holding one LF source file."""
    target = tmp_path / "module.py"
    target.write_text(_SOURCE, encoding="utf-8", newline="")
    monkeypatch.setattr(fs_mcp, "ALLOWED_DIRECTORIES", [f"{tmp_path}/"])
    monkeypatch.setattr(fs_mcp, "_EXCLUDED_ROOTS", [])
    return tmp_path


@pytest.fixture
def client():
    """The filesystem router with the admin gate satisfied."""
    app = FastAPI()
    app.include_router(fs_mcp.router, prefix="/api/filesystem")
    app.dependency_overrides[fs_mcp.check_admin_permission] = lambda: True
    with TestClient(app) as test_client:
        yield test_client


def _edit(client, path: Path, edits: list):
    return client.post("/api/filesystem/mcp/edit_file", json={"path": str(path), "edits": edits})


def _raw(path: Path) -> bytes:
    return path.read_bytes()


def test_a_missing_old_text_fails_with_similarity_and_line_range(client, workspace):
    target = workspace / "module.py"
    before = _raw(target)
    response = _edit(client, target, [{"old_text": "def beta():\n    return 3", "new_text": "x"}])
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "edit 0: old_text not found" in detail
    assert "lines 5-6" in detail
    assert "similarity 0.9" in detail
    assert "-    return 2" in detail and "+    return 3" in detail
    assert _raw(target) == before


def test_a_duplicate_old_text_fails_by_default_and_succeeds_when_expected(client, workspace):
    target = workspace / "module.py"
    before = _raw(target)
    edit = {"old_text": "    return", "new_text": "    yield"}
    response = _edit(client, target, [edit])
    assert response.status_code == 400
    assert "occurs 2 times but expected_replacements is 1" in response.json()["detail"]
    assert _raw(target) == before

    response = _edit(client, target, [{**edit, "expected_replacements": 2}])
    assert response.status_code == 200, response.text
    assert response.json()["changes"][0]["replacements"] == 2
    assert target.read_text(encoding="utf-8").count("    yield") == 2


def test_editing_a_crlf_file_keeps_every_line_ending_crlf(client, workspace):
    target = workspace / "module.py"
    target.write_bytes(_SOURCE.replace("\n", "\r\n").encode("utf-8"))
    edits = [{"old_text": "def alpha():\n    return 1\n", "new_text": "def alpha():\n    x = 1\n    return x\n"}]
    response = _edit(client, target, edits)
    assert response.status_code == 200, response.text
    raw = _raw(target)
    assert b"    x = 1\r\n    return x\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")


def test_a_miss_emits_one_edit_miss_record_without_file_content(client, workspace, caplog):
    target = workspace / "module.py"
    with caplog.at_level(logging.DEBUG):
        _edit(client, target, [{"old_text": "def gamma_unique_marker():", "new_text": "pass"}])
    misses = [r for r in caplog.records if getattr(r, "edit_miss", None)]
    assert len(misses) == 1
    event = misses[0].edit_miss
    assert event["extension"] == ".py" and event["edit_index"] == 0
    assert event["reason"] == "not_found" and 0 <= event["similarity"] <= 1
    for record in caplog.records:
        text = record.getMessage() + repr(record.__dict__)
        assert "gamma_unique_marker" not in text
        assert "return 1" not in text and "def alpha" not in text


def test_a_multi_edit_call_is_all_or_nothing(client, workspace):
    target = workspace / "module.py"
    before = _raw(target)
    edits = [
        {"old_text": "return 1", "new_text": "return 10"},
        {"old_text": "not in the file", "new_text": "anything"},
    ]
    response = _edit(client, target, edits)
    assert response.status_code == 400
    assert "edit 1:" in response.json()["detail"]
    assert _raw(target) == before


def test_mixed_line_endings_are_matched_as_is():
    assert detect_line_ending("a\r\nb\r\n") == "\r\n"
    assert detect_line_ending("a\r\nb\n") == "\n"
    assert detect_line_ending("no newline") == "\n"


def test_closest_span_reports_the_best_window():
    span = find_closest_span(_SOURCE, "    return 2")
    assert (span.start_line, span.end_line, span.similarity) == (6, 6, 1.0)
