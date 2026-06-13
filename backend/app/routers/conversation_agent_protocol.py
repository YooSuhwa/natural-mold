from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.event_broker import registry as broker_registry
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers.conversation_agent_protocol_commands import handle_thread_command
from app.routers.conversation_agent_protocol_contracts import (
    AgentCommandRequest,
    HistoryRequest,
    SubscribeRequest,
    UpdateStateRequest,
    protocol_headers,
    state_response,
)
from app.routers.conversation_agent_protocol_replay import (
    load_protocol_events,
    protocol_replay_generator,
)
from app.routers.conversation_agent_protocol_runtime import (
    get_owned_thread,
    load_thread_state_values,
    protocol_broker_generator,
)
from app.services import conversation_run_service
from app.services.conversation_run_worker import start_conversation_run
from app.services.conversation_stream_service import sse_response

router = APIRouter(tags=["conversations"])


@router.post("/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/commands")
async def post_thread_command(
    conversation_id: uuid.UUID,
    thread_id: str,
    command: AgentCommandRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> JSONResponse:
    conversation = await get_owned_thread(
        db,
        conversation_id=conversation_id,
        thread_id=thread_id,
        user_id=user.id,
    )
    return await handle_thread_command(
        conversation=conversation,
        command=command,
        request=request,
        db=db,
        user=user,
        start_run=start_conversation_run,
    )


@router.get("/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/state")
async def get_thread_state(
    conversation_id: uuid.UUID,
    thread_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, object]:
    conversation = await get_owned_thread(
        db,
        conversation_id=conversation_id,
        thread_id=thread_id,
        user_id=user.id,
    )
    return state_response(conversation, values=await load_thread_state_values(conversation))


@router.post("/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/state")
async def update_thread_state(
    conversation_id: uuid.UUID,
    thread_id: str,
    request: UpdateStateRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> dict[str, object]:
    conversation = await get_owned_thread(
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
) -> list[dict[str, object]]:
    conversation = await get_owned_thread(
        db,
        conversation_id=conversation_id,
        thread_id=thread_id,
        user_id=user.id,
    )
    values = await load_thread_state_values(conversation)
    return [state_response(conversation, values=values) for _ in range(min(request.limit, 1))]


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
    conversation = await get_owned_thread(
        db,
        conversation_id=conversation_id,
        thread_id=thread_id,
        user_id=user.id,
    )
    after_id = last_event_id or last_event_id_header
    current_run = await conversation_run_service.current_run_for_conversation(
        db,
        conversation_id=conversation.id,
    )
    if current_run is not None and current_run.is_active:
        broker = broker_registry.get(str(current_run.id))
        if broker is not None and not broker.is_closed:
            return sse_response(
                protocol_broker_generator(
                    broker,
                    thread_id=str(conversation.id),
                    params=request.as_params(),
                    after_id=after_id,
                ),
                extra_headers=protocol_headers(mode="live", run_id=str(current_run.id)),
            )
        return JSONResponse(
            {
                "error": "RUN_ATTACH_RETRY",
                "message": "Run is active but its live event stream is not attached yet.",
                "run_id": str(current_run.id),
            },
            status_code=409,
            headers=protocol_headers(run_id=str(current_run.id)),
        )

    events = await load_protocol_events(db, conversation.id)
    return sse_response(
        protocol_replay_generator(
            events,
            request.as_params(),
            after_id=after_id,
        ),
        extra_headers=protocol_headers(mode="replay"),
    )


@router.get("/api/conversations/{conversation_id}/langgraph/state")
async def get_compat_thread_state(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, object]:
    conversation = await get_owned_thread(
        db,
        conversation_id=conversation_id,
        thread_id=str(conversation_id),
        user_id=user.id,
    )
    state = state_response(conversation, values=await load_thread_state_values(conversation))
    return {
        "thread_id": str(conversation.id),
        "values": state["values"],
        "messages": state["values"]["messages"],
        "interrupts": [],
        "checkpoint_by_message_id": {},
        "active_run": None,
        "latest_run": None,
    }
