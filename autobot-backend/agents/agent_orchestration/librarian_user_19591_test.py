# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Agent orchestration reads the KB as the verified triggering user, else nothing (#19591, #16665)."""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.agent_orchestration.agent_execution import AgentExecutor
from agents.agent_orchestration.reading_user import reading_user
from agents.agent_orchestration.types import AgentType
from agents.kb_librarian_agent import KBLibrarianAgent
from knowledge.read_path_fakes_16665 import PRIVATE_A, PRIVATE_B, SHARED, FakeKB

BACKEND = Path(__file__).resolve().parents[2]
LIBRARIAN_MARKER = "librarian"


def _librarian() -> KBLibrarianAgent:
    agent = object.__new__(KBLibrarianAgent)
    agent.knowledge_base, agent.enabled = FakeKB(), True
    agent.max_results, agent.similarity_threshold, agent.auto_summarize = 5, 0.0, False
    return agent


def _executor(rag=None) -> AgentExecutor:
    executor = object.__new__(AgentExecutor)
    librarian = _librarian()
    executor._get_kb_librarian = lambda: librarian
    executor._get_rag_agent = lambda: rag
    executor._specialized_getters = {}
    return executor


def _rag_recorder():
    rag = MagicMock()
    rag.process_document_query = AsyncMock(side_effect=lambda req, docs, ctx: {"docs": docs})
    return rag


async def _retrieve(user_id, context=None):
    return await _executor()._execute_single_agent(AgentType.KNOWLEDGE_RETRIEVAL, "q", context, None, user_id)


async def _rag(user_id, context=None):
    return await _executor(_rag_recorder())._execute_single_agent(AgentType.RAG, "q", context, None, user_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("run", [_retrieve, _rag])
async def test_the_triggering_user_gets_their_facts_and_never_anothers(run):
    text = repr(await run("alice"))
    assert PRIVATE_A in text and SHARED in text
    assert PRIVATE_B not in text
    assert PRIVATE_A not in repr(await run("bob"))


@pytest.mark.asyncio
@pytest.mark.parametrize("run", [_retrieve, _rag])
async def test_with_no_user_the_librarian_still_denies(run):
    text = repr(await run(None))
    assert not any(fact in text for fact in (PRIVATE_A, PRIVATE_B, SHARED))


@pytest.mark.asyncio
async def test_a_user_named_in_context_is_never_trusted():
    text = repr(await _retrieve(None, context={"user_id": "alice", "metadata": {"user_id": "alice"}}))
    assert PRIVATE_A not in text


@pytest.mark.asyncio
async def test_the_multi_agent_secondary_step_reads_as_the_same_user():
    executor = _executor()
    routing = {"primary_agent": AgentType.KNOWLEDGE_RETRIEVAL, "secondary_agents": [AgentType.KNOWLEDGE_RETRIEVAL]}
    executor._synthesize_multi_agent_results = AsyncMock(side_effect=lambda p, s, r: {"p": p, "s": s})
    executor.router = MagicMock(adapt_request_for_secondary=lambda *a: "q2")

    text = repr(await executor._execute_multi_agent(routing, "q", None, None, "alice"))

    assert text.count(PRIVATE_A) >= 2 and PRIVATE_B not in text


@pytest.mark.parametrize("value", [None, "", "   ", 7, {"id": "a"}])
def test_reading_user_is_the_verified_string_or_nobody(value):
    assert reading_user(value) is None


def test_reading_user_keeps_a_real_user():
    assert reading_user("alice") == "alice"


@pytest.mark.asyncio
async def test_an_a2a_peer_is_not_a_local_user_so_the_librarian_gets_none():
    from a2a import task_executor

    coordinator = MagicMock(process_request=AsyncMock(return_value={"status": "refused"}))
    manager = MagicMock()
    hostile = {"user_id": "alice"}
    with (
        patch("agents.agent_orchestration.get_distributed_agent_coordinator", return_value=coordinator),
        patch.object(task_executor, "_report_capability_refusal"),
        patch.object(task_executor, "_scrub_inbound", return_value="q"),
        patch.object(task_executor, "_abort_if_cancelled", AsyncMock(return_value=False)),
    ):
        await task_executor._execute_claimed("t1", "q", hostile, 0.6, "peer", manager)

    assert coordinator.process_request.await_args.kwargs.get("user_id") is None


#: ``_register_specialized_handlers`` calls the data-analysis, translation, ... agents as ``g().process_query``.
#: They are LLM-prompt agents with no KB read, so there is no user to name (checked in #19591).
NON_KB_RECEIVERS = {"g()"}


def _is_librarian_call(node: ast.Call) -> bool:
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "process_query"):
        return False
    if ast.unparse(func.value) in NON_KB_RECEIVERS:
        return False
    return not (isinstance(func.value, ast.Name) and func.value.id in {"self", "cls", "super"})


def _unscoped_calls(paths):
    """(path, line, has_user_id) for each non-self ``.process_query(`` call in a librarian-aware file."""
    found = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        if LIBRARIAN_MARKER not in source.lower():
            continue
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and _is_librarian_call(node):
                found.append((path, node.lineno, any(k.arg == "user_id" for k in node.keywords)))
    return found


def _production_files(root: Path):
    skip = ("_test.py", "test_", "conftest")
    return [p for p in root.rglob("*.py") if not p.name.startswith(skip[1:]) and not p.name.endswith(skip[0])]


def test_every_librarian_process_query_in_the_backend_names_its_user():
    calls = _unscoped_calls(_production_files(BACKEND))
    assert any(p.name == "reading_user.py" for p, _, _ in calls), "walk found no librarian call: instrument is blind"
    assert any(p.name == "kb_librarian.py" for p, _, _ in calls)
    bare = [f"{p.relative_to(BACKEND)}:{line}" for p, line, ok in calls if not ok]
    assert not bare, f"librarian process_query without user_id=: {bare}"


def test_agent_orchestration_reaches_the_librarian_only_through_the_one_helper():
    orchestration = Path(__file__).parent
    direct = [
        f"{p.name}:{line}"
        for p in _production_files(orchestration)
        if p.name != "reading_user.py"
        for line in _lines_calling_librarian(p)
    ]
    assert not direct, f"call the librarian through reading_user.librarian_query: {direct}"


def _lines_calling_librarian(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_librarian_call(node) and "librarian" in ast.unparse(node.func.value):
            yield node.lineno
