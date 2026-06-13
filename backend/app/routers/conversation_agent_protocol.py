from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.event_broker import registry as broker_registry
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers.conversation_agent_protocol_attach import wait_for_live_broker_or_terminal
from app.routers.conversation_agent_protocol_commands import handle_thread_command
from app.routers.conversation_agent_protocol_contracts import (
    AgentCommandRequest,
    HistoryRequest,
    SubscribeRequest,
    UpdateStateRequest,
    protocol_headers,
    state_response,
)
from app.routers.conversation_agent_protocol_interrupts import (
    interrupts_from_tasks,
    load_pending_interrupt_tasks,
)
from app.routers.conversation_agent_protocol_replay import (
    load_protocol_events,
    protocol_replay_generator,
)
from app.routers.conversation_agent_protocol_runtime import (
    get_owned_thread,
    load_thread_state_snapshot,
    protocol_broker_generator,
)
from app.routers.conversation_agent_protocol_stale import maybe_mark_stale_active_run
from app.routers.conversation_agent_protocol_thread_stream import (
    needs_thread_stream,
    protocol_thread_stream_generator,
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
    tasks = await load_pending_interrupt_tasks(db, conversation, user_id=user.id)
    snapshot = await load_thread_state_snapshot(conversation)
    return state_response(
        conversation,
        values=snapshot.values,
        tasks=tasks,
        checkpoint_by_message_id=snapshot.checkpoint_by_message_id,
    )


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
    snapshot = await load_thread_state_snapshot(conversation)
    return [
        state_response(
            conversation,
            values=snapshot.values,
            checkpoint_by_message_id=snapshot.checkpoint_by_message_id,
        )
        for _ in range(min(request.limit, 1))
    ]


@router.post("/api/conversations/{conversation_id}/langgraph/threads/{thread_id}/stream/events")
async def subscribe_thread_events(
    conversation_id: uuid.UUID,
    thread_id: str,
    request: SubscribeRequest,
    http_request: Request,
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
    params = request.as_params()
    if needs_thread_stream(params):
        return sse_response(
            protocol_thread_stream_generator(
                conversation_id=conversation.id,
                thread_id=str(conversation.id),
                params=params,
                after_id=after_id,
                is_disconnected=http_request.is_disconnected,
            ),
            extra_headers=protocol_headers(mode="thread"),
        )

    current_run = await conversation_run_service.current_run_for_conversation(
        db,
        conversation_id=conversation.id,
    )
    if current_run is not None and current_run.is_active:
        broker = broker_registry.get(str(current_run.id))
        if broker is None or broker.is_closed:
            current_run = await wait_for_live_broker_or_terminal(db, current_run.id)
            broker = (
                broker_registry.get(str(current_run.id))
                if current_run is not None and current_run.is_active
                else None
            )
        if current_run is None or not current_run.is_active:
            events = await load_protocol_events(db, conversation.id)
            return sse_response(
                protocol_replay_generator(
                    events,
                    params,
                    after_id=after_id,
                ),
                extra_headers=protocol_headers(mode="replay"),
            )
        if broker is not None and not broker.is_closed:
            return sse_response(
                protocol_broker_generator(
                    broker,
                    thread_id=str(conversation.id),
                    params=params,
                    after_id=after_id,
                ),
                extra_headers=protocol_headers(mode="live", run_id=str(current_run.id)),
            )
        stale = await maybe_mark_stale_active_run(
            db,
            run_id=current_run.id,
            conversation=conversation,
            user=user,
            request=http_request,
        )
        if stale is not None:
            return sse_response(
                protocol_replay_generator(
                    stale.events,
                    params,
                    after_id=after_id,
                    final_events=[stale.stale_event],
                ),
                extra_headers=protocol_headers(mode="stale", run_id=str(stale.run.id)),
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
            params,
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
    tasks = await load_pending_interrupt_tasks(db, conversation, user_id=user.id)
    snapshot = await load_thread_state_snapshot(conversation)
    state = state_response(
        conversation,
        values=snapshot.values,
        tasks=tasks,
        checkpoint_by_message_id=snapshot.checkpoint_by_message_id,
    )
    return {
        "thread_id": str(conversation.id),
        "values": state["values"],
        "messages": state["values"]["messages"],
        "interrupts": interrupts_from_tasks(tasks),
        "checkpoint_by_message_id": snapshot.checkpoint_by_message_id,
        "active_run": None,
        "latest_run": None,
    }
