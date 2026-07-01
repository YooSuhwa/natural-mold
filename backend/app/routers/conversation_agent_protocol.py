from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.event_broker import registry as broker_registry
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers import conversation_agent_protocol_state as state_api
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
    protocol_broker_generator,
)
from app.routers.conversation_agent_protocol_stale import maybe_mark_stale_active_run
from app.routers.conversation_agent_protocol_state_snapshot import (
    collect_state_secret_values,
    load_thread_state_snapshot,
)
from app.routers.conversation_agent_protocol_thread_stream import (
    needs_thread_stream,
    protocol_thread_stream_generator,
)
from app.services import conversation_run_service
from app.services.conversation_run_worker import start_conversation_run
from app.services.conversation_stream_service import sse_response

router = APIRouter(tags=["conversations"])

ACTIVE_THREAD_NEXT = "__moldy_active_run__"


def _run_metadata(run: object | None) -> dict[str, object] | None:
    if run is None:
        return None
    run_id = getattr(run, "id", None)
    status = getattr(run, "status", None)
    if run_id is None or not isinstance(status, str):
        return None
    metadata: dict[str, object] = {"id": str(run_id), "status": status}
    if status == "failed":
        error_code = getattr(run, "error_code", None)
        if isinstance(error_code, str) and error_code:
            metadata["error_code"] = error_code
        # error_message는 stream_error(모델/provider 실패)일 때만 채팅 에러 버블에
        # 노출한다. 이 값은 worker가 public_stream_error_message(블록리스트) + run
        # credential 값 기반 마스킹을 거친다(_redact_run_error_message). runtime_error
        # (스트림 바깥 인프라 예외)는 파일경로/DB호스트 등 내부 토폴로지가 섞일 수
        # 있고 위 2단 마스킹으로도 안 가려지므로, 채팅 UI엔 프론트 폴백 문구만 보이게
        # 하고 마스킹된 상세는 저장값(GET /runs/{id}, 운영/디버그)에만 둔다.
        # stale/canceled의 내부 사유도 프론트가 자체 문구로 표시하므로 노출하지 않는다.
        error_message = getattr(run, "error_message", None)
        if error_code == "stream_error" and isinstance(error_message, str) and error_message:
            metadata["error_message"] = error_message
    return metadata


def _active_thread_next_nodes(run: object | None) -> list[str]:
    status = getattr(run, "status", None)
    return [ACTIVE_THREAD_NEXT] if status in {"queued", "running", "canceling"} else []


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
    request: Request,
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
    secrets = await collect_state_secret_values(db, conversation)
    snapshot = await load_thread_state_snapshot(conversation, db=db, secret_values=secrets)
    current_run = await conversation_run_service.current_run_for_conversation(
        db,
        conversation_id=conversation.id,
    )
    latest_run = await conversation_run_service.latest_run_for_conversation(
        db,
        conversation_id=conversation.id,
    )
    if current_run is not None and current_run.is_active:
        stale = await maybe_mark_stale_active_run(
            db,
            run_id=current_run.id,
            conversation=conversation,
            user=user,
            request=request,
        )
        if stale is not None:
            current_run = None
            latest_run = stale.run
    return state_response(
        conversation,
        values=snapshot.values,
        tasks=tasks,
        next_nodes=_active_thread_next_nodes(current_run),
        checkpoint_by_message_id=snapshot.checkpoint_by_message_id,
        parent_checkpoint_by_message_id=snapshot.parent_checkpoint_by_message_id,
        active_run=_run_metadata(current_run),
        latest_run=_run_metadata(latest_run),
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
    return await state_api.update_thread_state_response(
        db,
        conversation=conversation,
        request=request,
        user=user,
    )


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
    return await state_api.load_thread_history_response(conversation, request, db=db, user=user)


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
    _csrf: None = Depends(verify_csrf),
) -> Response:
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
    request: Request,
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
    secrets = await collect_state_secret_values(db, conversation)
    snapshot = await load_thread_state_snapshot(conversation, db=db, secret_values=secrets)
    current_run = await conversation_run_service.current_run_for_conversation(
        db,
        conversation_id=conversation.id,
    )
    latest_run = await conversation_run_service.latest_run_for_conversation(
        db,
        conversation_id=conversation.id,
    )
    if current_run is not None and current_run.is_active:
        stale = await maybe_mark_stale_active_run(
            db,
            run_id=current_run.id,
            conversation=conversation,
            user=user,
            request=request,
        )
        if stale is not None:
            current_run = None
            latest_run = stale.run
    state = state_response(
        conversation,
        values=snapshot.values,
        tasks=tasks,
        next_nodes=_active_thread_next_nodes(current_run),
        checkpoint_by_message_id=snapshot.checkpoint_by_message_id,
        parent_checkpoint_by_message_id=snapshot.parent_checkpoint_by_message_id,
        active_run=_run_metadata(current_run),
        latest_run=_run_metadata(latest_run),
    )
    return {
        "thread_id": str(conversation.id),
        "values": state["values"],
        "messages": state["values"]["messages"],
        "interrupts": interrupts_from_tasks(tasks),
        "checkpoint_by_message_id": snapshot.checkpoint_by_message_id,
        "parent_checkpoint_by_message_id": snapshot.parent_checkpoint_by_message_id,
        "active_run": _run_metadata(current_run),
        "latest_run": _run_metadata(latest_run),
    }
