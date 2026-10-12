# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
"""Client-callable cancel for a session's current chat turn (#17273, stop control).

POST /api/chat/sessions/{session_id}/turn/cancel

Owner-only (``validate_session_ownership``). Idempotent: with no turn running it
answers ``status="no_active_turn"`` instead of failing, so a stop button pressed
just as the reply finishes is not an error. The cancel runs the same teardown a
client disconnect does (``chat_workflow.turn_control``), so it reaches the
in-flight LLM call.
"""

from typing import Dict

from fastapi import APIRouter, Depends

from api.schemas_chat_turn import TurnCancelData
from api.schemas_common import DataResponse
from auth_middleware import get_current_user
from autobot_shared.error_boundaries import ErrorCategory, with_error_handling
from autobot_shared.logging_manager import get_logger
from chat_workflow.turn_control import REASON_CLIENT_CANCEL, cancel_turn
from security.session_ownership import validate_session_ownership

router = APIRouter(tags=["chat"])
logger = get_logger(__name__)


@router.post(
    "/chat/sessions/{session_id}/turn/cancel",
    response_model=DataResponse[TurnCancelData],
)
@with_error_handling(
    category=ErrorCategory.SERVER_ERROR,
    operation="cancel_chat_turn",
    error_code_prefix="CHAT_TURN",
)
async def cancel_chat_turn(
    session_id: str,
    current_user: Dict = Depends(get_current_user),
    ownership: Dict = Depends(validate_session_ownership),
):
    """Cancel the turn currently running for *session_id* (owner only)."""
    cancelled = await cancel_turn(session_id, REASON_CLIENT_CANCEL)
    logger.info("Turn cancel requested: session=%s cancelled=%d", session_id, cancelled)
    return DataResponse(
        data=TurnCancelData(
            session_id=session_id,
            status="cancelled" if cancelled else "no_active_turn",
            cancelled_turns=cancelled,
        )
    )
