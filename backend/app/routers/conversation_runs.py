from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import EventBroker, slice_events_after
from app.agent_runtime.event_broker import registry as broker_registry
from app.agent_runtime.streaming import format_sse
from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import conversation_not_found, resume_not_found
from app.models.conversation_run import RUN_ACTIVE_STATUSES, ConversationRun, utc_now_naive
from app.models.message_event import MessageEvent
from app.schemas.conversation_run import ConversationRunResponse
from app.services import chat_service, conversation_run_service, trace_storage
from app.services.conversation_audit_service import record_conversation_run_audit
from app.services.conversation_run_worker import get_run_task_registry
from app.services.conversation_stream_service import sse_response

router = APIRouter(tags=["conversations"])


def _normalize_event_id(raw: object) -> str | None:
    return raw if isinstance(raw, str) and raw else None


async def _broker_run_generator(
    broker: EventBroker, after_id: str | None
) -> AsyncGenerator[str, None]:
    effective_after_id = after_id
    if after_id and not broker.has_event_id(after_id):
        # last_event_id 가 ring buffer 에서 이미 evict 됨 — 이대로 subscribe 하면
        # replay 단계가 통째로 건너뛰어져 silent gap 이 된다. 누락 구간을 stale
        # 마커로 알리고, buffer 에 남아 있는 구간 전체를 replay 한다 (중복 이벤트는
        # 클라이언트 stream guard 가 id 기준으로 dedup).
        yield format_sse(
            event_names.STALE,
            {
                "reason": "broker_gap",
                "run_id": broker.run_id,
                "last_event_id": broker.last_event_id,
            },
        )
        effective_after_id = None
    async for evt in broker.subscribe(after_id=effective_after_id):
        yield format_sse(evt["event"], evt["data"], event_id=_normalize_event_id(evt.get("id")))


async def _replay_run_generator(
    record: MessageEvent,
    after_id: str | None,
) -> AsyncGenerator[str, None]:
    for evt in slice_events_after(record.events or [], after_id):
        evt_name = evt.get("event")
        if not isinstance(evt_name, str) or not evt_name:
            continue
        yield format_sse(
            evt_name,
            evt.get("data") or {},
            event_id=_normalize_event_id(evt.get("id")),
        )


async def _stale_only_generator(run: ConversationRun) -> AsyncGenerator[str, None]:
    yield format_sse(
        event_names.STALE,
        {
            "reason": "run_worker_lost",
            "run_id": str(run.id),
            "last_event_id": run.last_event_id,
        },
    )


def _run_is_stale(run: ConversationRun) -> bool:
    reference = run.heartbeat_at or run.started_at or run.created_at
    return reference <= utc_now_naive() - timedelta(seconds=settings.chat_run_stale_after_seconds)


def _retryable_attach_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "RUN_ATTACH_RETRY",
            "message": "Run is active but no local stream broker is available yet.",
        },
        headers={"Retry-After": "1"},
    )


@router.get(
    "/api/conversations/{conversation_id}/runs/active",
    response_model=ConversationRunResponse | None,
)
async def get_active_conversation_run(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if conv is None:
        raise conversation_not_found()
    run = await conversation_run_service.get_active_run(
        db,
        conversation_id=conversation_id,
        user_id=user.id,
    )
    return ConversationRunResponse.model_validate(run) if run is not None else None


@router.get("/api/conversations/{conversation_id}/runs/{run_id}/stream")
async def stream_conversation_run(
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    request: Request,
    last_event_id: str | None = Query(None),
    last_event_id_header: str | None = Header(None, alias="Last-Event-ID"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    after_id = last_event_id or last_event_id_header
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if conv is None:
        raise conversation_not_found()

    run = await conversation_run_service.get_run_for_user(
        db,
        conversation_id=conversation_id,
        run_id=run_id,
        user_id=user.id,
    )
    if run is None:
        raise resume_not_found()

    run_id_str = str(run_id)
    broker = broker_registry.get(run_id_str)
    if broker is not None and not broker.is_closed:
        if broker.conversation_id != str(conversation_id):
            raise resume_not_found()
        return sse_response(
            _broker_run_generator(broker, after_id),
            extra_headers={"X-Run-Id": run_id_str, "X-Resume-Mode": "live"},
        )

    if run.status in RUN_ACTIVE_STATUSES:
        if not _run_is_stale(run):
            raise _retryable_attach_error()
        await conversation_run_service.transition_run(
            db,
            run,
            "stale",
            error_code="worker_lost",
            error_message="Active run has no local worker and heartbeat is stale.",
        )
        await conversation_run_service.finalize_run_outputs_for_status(db, run, "stale")
        await record_conversation_run_audit(
            db,
            action="conversation.run_stale",
            run=run,
            user=user,
            request=request,
            status="stale",
        )
        await db.commit()
        return sse_response(
            _stale_only_generator(run),
            extra_headers={"X-Run-Id": run_id_str, "X-Resume-Mode": "stale"},
        )

    record = await trace_storage.get_trace_by_msg_id(db, run_id_str)
    if record is None or record.conversation_id != conversation_id:
        raise resume_not_found()
    return sse_response(
        _replay_run_generator(record, after_id),
        extra_headers={"X-Run-Id": run_id_str, "X-Resume-Mode": "replay"},
    )


@router.post(
    "/api/conversations/{conversation_id}/runs/{run_id}/cancel",
    response_model=ConversationRunResponse,
)
async def cancel_conversation_run(
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if conv is None:
        raise conversation_not_found()
    # for_update — worker 의 queued->running 전이와 직렬화해 stale read 기반
    # lost update 를 방지한다 (서비스 docstring 의 동시성 계약).
    run = await conversation_run_service.get_run_for_user(
        db,
        conversation_id=conversation_id,
        run_id=run_id,
        user_id=user.id,
        for_update=True,
    )
    if run is None:
        raise resume_not_found()

    previous_status = run.status
    run = await conversation_run_service.request_cancel_run(db, run)
    if previous_status in {"queued", "running"} and run.status == "canceling":
        await record_conversation_run_audit(
            db,
            action="conversation.run_cancel_request",
            run=run,
            user=user,
            request=request,
            status="canceling",
        )
    await db.commit()

    if previous_status in {"queued", "running"} and run.status == "canceling":
        registry = get_run_task_registry()
        local_cancel_sent = registry.request_cancel(run_id, reason="user")
        if not local_cancel_sent:
            await db.refresh(run, with_for_update=True)
            if run.status == "canceling":
                await conversation_run_service.transition_run(db, run, "canceled")
                await conversation_run_service.finalize_run_outputs_for_status(
                    db,
                    run,
                    "canceled",
                    append_terminal_event=True,
                )
                await record_conversation_run_audit(
                    db,
                    action="conversation.run_canceled",
                    run=run,
                    user=user,
                    request=request,
                    status="canceled",
                )
                await db.commit()

    await db.refresh(run)
    return ConversationRunResponse.model_validate(run)


@router.get(
    "/api/conversations/{conversation_id}/runs/{run_id}",
    response_model=ConversationRunResponse,
)
async def get_conversation_run(
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if conv is None:
        raise conversation_not_found()
    run = await conversation_run_service.get_run_for_user(
        db,
        conversation_id=conversation_id,
        run_id=run_id,
        user_id=user.id,
    )
    if run is None:
        raise resume_not_found()
    return ConversationRunResponse.model_validate(run)
