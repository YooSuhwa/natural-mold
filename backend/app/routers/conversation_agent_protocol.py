from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.event_broker import registry as broker_registry
from app.agent_runtime.executor import execute_agent_stream_langgraph
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
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
from app.routers.conversation_agent_protocol_runtime import (
    SUPPORTED_MULTITASK_STRATEGIES,
    cfg_agent_uuid,
    checkpoint_id,
    command_multitask_strategy,
    get_owned_thread,
    input_preview,
    load_thread_state_values,
    protocol_broker_generator,
)
from app.services import chat_service, conversation_run_service
from app.services.conversation_audit_service import record_conversation_audit
from app.services.conversation_run_worker import start_conversation_run
from app.services.conversation_stream_service import resolve_agent_context, sse_response

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
    if command.method not in SUPPORTED_COMMAND_METHODS:
        return command_error(
            command,
            code="UNSUPPORTED_COMMAND",
            message=f"Unsupported command method: {command.method}",
        )
    strategy = command_multitask_strategy(command)
    if strategy not in SUPPORTED_MULTITASK_STRATEGIES:
        return command_error(
            command,
            code="UNSUPPORTED_MULTITASK_STRATEGY",
            message=f"Unsupported multitask strategy: {strategy}",
        )

    input_payload = command.params.input or {}
    preview = input_preview(input_payload)
    cfg = await resolve_agent_context(
        db,
        conversation.id,
        user,
        checkpoint_id=checkpoint_id(command),
    )
    if preview:
        await chat_service.maybe_set_auto_title(db, conversation.id, preview)
    await chat_service.touch_conversation(db, conversation.id)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_send",
        conversation_id=conversation.id,
        agent_id=cfg_agent_uuid(conversation),
        metadata={
            "content_length": len(preview or ""),
            "attachment_count": 0,
            "source": "langgraph_protocol",
        },
    )
    try:
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation.id,
            agent_id=cfg_agent_uuid(conversation),
            user_id=user.id,
            source="chat",
            input_preview=preview,
            metadata={
                "protocol": "langgraph_v3",
                "command_id": command.id,
                "assistant_id": command.params.assistant_id,
            },
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            return command_error(
                command,
                code="MULTITASK_REJECTED",
                message=str(exc.detail),
            )
        raise
    run_id = run.id
    await db.commit()

    await start_conversation_run(
        run_id=run_id,
        conversation_id=conversation.id,
        cfg=cfg,
        user=user,
        input_payload=input_payload,
        moldy_source="chat",
        executor_fn=execute_agent_stream_langgraph,
    )
    return command_success(
        command,
        conversation=conversation,
        thread_id=thread_id,
        run_id=str(run_id),
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
