from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import conversation_not_found
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_contracts import (
    SUPPORTED_COMMAND_METHODS,
    AgentCommandRequest,
    HistoryRequest,
    SubscribeRequest,
    UpdateStateRequest,
    command_error,
    command_success,
    protocol_headers,
    state_response,
)
from app.routers.conversation_agent_protocol_replay import (
    load_protocol_events,
    protocol_replay_generator,
)
from app.services import chat_service
from app.services.conversation_stream_service import sse_response

router = APIRouter(tags=["conversations"])


async def _get_owned_thread(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    thread_id: str,
    user_id: uuid.UUID,
) -> Conversation:
    if thread_id != str(conversation_id):
        raise conversation_not_found()

    conversation = await chat_service.get_owned_conversation(db, conversation_id, user_id)
    if conversation is None:
        raise conversation_not_found()
    return conversation


@router.post("/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/commands")
async def post_thread_command(
    conversation_id: uuid.UUID,
    thread_id: str,
    command: AgentCommandRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> JSONResponse:
    conversation = await _get_owned_thread(
        db,
        conversation_id=conversation_id,
        thread_id=thread_id,
        user_id=user.id,
    )
    if command.method not in SUPPORTED_COMMAND_METHODS:
        return command_error(
            command,
            code="UNSUPPORTED_COMMAND",
            message=f"Unsupported command method: {command.method}",
        )
    return command_success(command, conversation=conversation, thread_id=thread_id)


@router.get("/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/state")
async def get_thread_state(
    conversation_id: uuid.UUID,
    thread_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    conversation = await _get_owned_thread(
        db,
        conversation_id=conversation_id,
        thread_id=thread_id,
        user_id=user.id,
    )
    return state_response(conversation)


@router.post("/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/state")
async def update_thread_state(
    conversation_id: uuid.UUID,
    thread_id: str,
    request: UpdateStateRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> dict[str, Any]:
    conversation = await _get_owned_thread(
        db,
        conversation_id=conversation_id,
        thread_id=thread_id,
        user_id=user.id,
    )
    return state_response(conversation, values=request.values)


@router.post("/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/history")
async def get_thread_history(
    conversation_id: uuid.UUID,
    thread_id: str,
    request: HistoryRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> list[dict[str, Any]]:
    conversation = await _get_owned_thread(
        db,
        conversation_id=conversation_id,
        thread_id=thread_id,
        user_id=user.id,
    )
    return [state_response(conversation) for _ in range(min(request.limit, 1))]


@router.post("/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/stream/events")
async def subscribe_thread_events(
    conversation_id: uuid.UUID,
    thread_id: str,
    request: SubscribeRequest,
    last_event_id: str | None = Query(None),
    last_event_id_header: str | None = Header(None, alias="Last-Event-ID"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    conversation = await _get_owned_thread(
        db,
        conversation_id=conversation_id,
        thread_id=thread_id,
        user_id=user.id,
    )
    events = await load_protocol_events(db, conversation.id)
    return sse_response(
        protocol_replay_generator(
            events,
            request.as_params(),
            after_id=last_event_id or last_event_id_header,
        ),
        extra_headers=protocol_headers(mode="replay"),
    )


@router.get("/api/conversations/{conversation_id}/langgraph/state")
async def get_compat_thread_state(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    conversation = await _get_owned_thread(
        db,
        conversation_id=conversation_id,
        thread_id=str(conversation_id),
        user_id=user.id,
    )
    state = state_response(conversation)
    return {
        "thread_id": str(conversation.id),
        "values": state["values"],
        "messages": state["values"]["messages"],
        "interrupts": [],
        "checkpoint_by_message_id": {},
        "active_run": None,
        "latest_run": None,
    }
