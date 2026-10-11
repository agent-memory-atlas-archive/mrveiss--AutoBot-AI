# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""READ-ONLY access to the retired per-turn transcript files (#18293).

Until #18293 every chat turn was also appended to
``data/conversation_transcripts/{id}.json`` by ``chat_workflow/conversation.py``
-- a second durable copy of what ``chat_sessions`` already holds. Nothing writes
there any more; ``store_authority`` declares the directory as
``legacy_conversation_transcripts`` with no write sites.

The files that exist stay readable, because some sessions have turns ONLY there
(they predate #13214, and #13293's repair was never run for them). Two readers
remain, both here so the location has one home:

* :func:`load_legacy_context_pairs` -- the LLM-context fallback for a session
  that has no completed turns in ``chat_sessions``.
* :func:`read_legacy_transcript_pairs` -- the full history, for the #13293
  repair path (``chat_history/session_reply_backfill.py``).

Moving these files into ``chat_sessions`` and retiring the directory is a data
move: it is proposed through the #17038 review queue, never done by code here.
Nothing in this module opens a file for writing. :func:`remove_legacy_transcript`
is the one removal, and it has exactly two callers, each of which decides first:
the session's verified owner deleting their own session
(``api/legacy_transcript_disposal.py``), and an APPROVED #17038 proposal
(``services/legacy_transcript_disposal_action.py``).
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Dict, List, Set

import aiofiles

from autobot_shared.logging_manager import get_logger
from autobot_shared.security.path_validator import validate_relative_path
from autobot_shared.store_authority import legacy_conversation_transcripts_dir

logger = get_logger(__name__)

#: Sessions whose LLM context has already been served from a legacy file in
#: this process -- so the fallback is logged once per session, not per load.
_LOGGED_SESSIONS: Set[str] = set()


def _pairs_from_transcript(transcript: object) -> List[Dict[str, str]]:
    """The ``{user, assistant}`` pairs of a parsed transcript, oldest first."""
    messages = transcript.get("messages", []) if isinstance(transcript, dict) else []
    if not isinstance(messages, list):
        return []
    return [
        {"user": m.get("user", ""), "assistant": m.get("assistant", "")}
        for m in messages
        if isinstance(m, dict) and (m.get("user") or "").strip()
    ]


def legacy_transcript_file(session_id: str) -> Path | None:
    """The existing legacy transcript of *session_id*, or None (none, or an unsafe id)."""
    path = _transcript_path(session_id, None)
    return path if path is not None and path.is_file() else None


async def remove_legacy_transcript(session_id: str) -> dict:
    """Unlink *session_id*'s legacy transcript. The caller has ALREADY decided it may.

    Only for an owner-verified session delete or an approved #17038 proposal (module
    docstring). Path-validated twice: the shared validator, then the realpath
    containment check CodeQL credits in the sink's own scope (#16229, #16236).
    """
    result: dict = {"transcript_deleted": False, "error": None}
    try:
        if _transcript_path(session_id, None) is None:
            raise ValueError("Invalid session ID")
        root = legacy_conversation_transcripts_dir()
        real = os.path.realpath(str(validate_relative_path(f"{session_id}.json", root)))
        if not real.startswith(os.path.realpath(str(root)) + os.sep):
            raise ValueError("Invalid session ID")
        if await asyncio.to_thread(os.path.exists, real):
            await asyncio.to_thread(os.remove, real)
            result["transcript_deleted"] = True
            logger.info("Removed legacy transcript of session %s", session_id)
    except Exception as exc:
        logger.warning("Failed to remove legacy transcript of session %s: %s", session_id, exc)
        result["error"] = str(exc)
    return result


def _transcript_path(session_id: str, transcript_dir: str | Path | None) -> Path | None:
    """The file for *session_id*, or None for an id that could leave the directory."""
    if not session_id or any(part in session_id for part in ("/", "\\", "..")):
        return None
    base = Path(transcript_dir) if transcript_dir is not None else legacy_conversation_transcripts_dir()
    return base / f"{session_id}.json"


async def read_legacy_transcript_pairs(
    session_id: str, transcript_dir: str | Path | None = None
) -> List[Dict[str, str]]:
    """Every exchange in *session_id*'s legacy transcript, or ``[]`` when there is none.

    Missing, unreadable and malformed files all read as ``[]`` -- a legacy file
    is never repaired, renamed or rewritten from here.
    """
    path = _transcript_path(session_id, transcript_dir)
    if path is None:
        return []
    try:
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            content = await f.read()
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("Failed to read legacy transcript file %s: %s", path, exc)
        return []
    try:
        return _pairs_from_transcript(json.loads(content))
    except json.JSONDecodeError:
        logger.warning("Malformed legacy transcript file %s -- ignoring it", path)
        return []


async def load_legacy_context_pairs(
    session_id: str, limit: int, transcript_dir: str | Path | None = None
) -> List[Dict[str, str]]:
    """The last *limit* legacy exchanges, for a session ``chat_sessions`` has no turns for."""
    pairs = (await read_legacy_transcript_pairs(session_id, transcript_dir))[-limit:] if limit > 0 else []
    if pairs and session_id not in _LOGGED_SESSIONS:
        _LOGGED_SESSIONS.add(session_id)
        logger.info(
            "LLM context for session %s served from the legacy transcript file (%d exchanges): "
            "it has no turns in chat_sessions. Disposition pending in the #17038 review queue (#18293).",
            session_id,
            len(pairs),
        )
    return pairs
