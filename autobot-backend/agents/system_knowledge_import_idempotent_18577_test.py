# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Re-importing the system-knowledge templates never inserts a second copy of a fact (#18577).

The librarian's document bodies embed a timestamp, so the KB's content-hash dedup cannot match a
re-import. The managers therefore store every template item under a stable id, and a repeat import
updates the existing fact. This runs the REAL template tree through the REAL manager and librarian
against an in-memory KB.
"""

from unittest.mock import AsyncMock, patch

import pytest
import yaml

from agents.kb_librarian.librarian import KBLibrarian
from agents.kb_librarian.stable_ids import system_fact_id
from agents.system_knowledge_manager import SystemKnowledgeManager
from autobot_shared.paths import project_root
from autobot_shared.system_knowledge_paths import system_knowledge_templates_dir


class _FakeKB:
    def __init__(self) -> None:
        self.facts: dict[str, str] = {}
        self.stored = 0
        self.updated = 0

    def get_fact(self, fact_id):
        return {"id": fact_id} if fact_id in self.facts else None

    async def store_fact(self, content, metadata=None, fact_id=None):
        self.stored += 1
        # Mirrors the real store: no stable id means a fresh random one every call.
        self.facts[fact_id or f"random-{self.stored}"] = content
        return {"status": "success"}

    async def update_fact(self, fact_id, content=None, metadata=None):
        self.updated += 1
        self.facts[fact_id] = content
        return {"status": "success"}


def _manager(kb: _FakeKB, tmp_path) -> SystemKnowledgeManager:
    manager = object.__new__(SystemKnowledgeManager)
    manager.knowledge_base = kb
    manager.librarian = KBLibrarian.__new__(KBLibrarian)
    manager.librarian.knowledge_base = kb
    manager.system_knowledge_dir = system_knowledge_templates_dir()
    manager.runtime_knowledge_dir = tmp_path / "runtime"
    manager.backup_dir = tmp_path / "backup"
    return manager


@pytest.mark.asyncio
async def test_second_import_of_the_real_templates_adds_no_fact(tmp_path) -> None:
    kb = _FakeKB()
    manager = _manager(kb, tmp_path)
    with patch("agents.kb_librarian.librarian.publish_event", new=AsyncMock()):
        await manager._import_system_knowledge()
        first = dict.fromkeys(kb.facts)
        assert first, "the template tree imported nothing"
        await manager._import_system_knowledge()
    assert set(kb.facts) == set(first), "a re-import created new facts"
    assert kb.updated == len(first)


def test_the_template_tree_is_the_tracked_resources_dir() -> None:
    assert system_knowledge_templates_dir() == project_root() / "autobot-backend" / "resources" / "knowledge"
    assert (system_knowledge_templates_dir() / "tools").is_dir()


def test_every_template_loads_as_a_mapping() -> None:
    """A comment-only stub (loads as ``None``) would crash or pollute the importers (#18577)."""
    for path in system_knowledge_templates_dir().rglob("*.yaml"):
        assert isinstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict), path


def test_system_fact_id_is_stable_and_kind_scoped() -> None:
    assert system_fact_id("tool", "nmap") == system_fact_id("tool", "nmap")
    assert system_fact_id("tool", "nmap") != system_fact_id("workflow", "nmap")
