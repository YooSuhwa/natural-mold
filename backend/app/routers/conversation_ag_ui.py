from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_adapter import (
    AG_UI_PROTOCOL_HEADER,
    format_ag_ui_sse,
    is_ag_ui_event_id,
    slice_ag_ui_events_after,
    source_event_id_from_ag_ui,
    stale_run_event,
)
from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.event_broker import registry as broker_registry
from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import conversation_not_found, resume_not_found
from app.models.conversation_run import RUN_ACTIVE_STATUSES, ConversationRun
from app.models.message_event import MessageEvent
from app.routers.conversation_runs import _retryable_attach_error, _run_is_stale
from app.services import chat_service, conversation_run_service, trace_storage
from app.services.conversation_audit_service import record_conversation_run_audit
from app.services.conversation_stream_service import sse_response

router = APIRouter(tags=["conversations"])


async def _broker_ag_ui_generator(
    broker: EventBroker,
    after_id: str | None,
    *,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
) -> AsyncGenerator[str, None]:
    source_after_id = source_event_id_from_ag_ui(after_id)
    source_in_buffer = broker.has_event_id(source_after_id)
    exact_ag_ui_after_id = after_id if is_ag_ui_event_id(after_id) and source_in_buffer else None
    gap_detected = bool(after_id) and not source_in_buffer
    broker_after_id = None if exact_ag_ui_after_id or gap_detected else source_after_id
    seen_after = exact_ag_ui_after_id is None

    if gap_detected:
        # after_id 의 source 이벤트가 ring buffer 에서 evict 됨 — Moldy SSE 의
        # broker_gap degrade 와 대칭으로 stale 마커를 먼저 보내고, buffer 에
        # 남아 있는 구간 전체를 replay 한다 (silent gap 방지).
        for ag_ui_event in slice_ag_ui_events_after(
            [
                stale_run_event(
                    str(run_id),
                    reason="broker_gap",
                    last_event_id=broker.last_event_id,
                )
            ],
            None,
            thread_id=str(conversation_id),
            run_id=str(run_id),
        ):
            yield format_ag_ui_sse(ag_ui_event)

    async for evt in broker.subscribe(after_id=broker_after_id):
        for ag_ui_event in slice_ag_ui_events_after(
            [evt],
            None,
            thread_id=str(conversation_id),
            run_id=str(run_id),
        ):
            if not seen_after:
                if ag_ui_event["id"] == exact_ag_ui_after_id:
                    seen_after = True
                continue
            yield format_ag_ui_sse(ag_ui_event)


async def _replay_ag_ui_generator(
    record: MessageEvent,
    after_id: str | None,
    *,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
) -> AsyncGenerator[str, None]:
    for evt in slice_ag_ui_events_after(
        record.events or [],
        after_id,
        thread_id=str(conversation_id),
        run_id=str(run_id),
    ):
        yield format_ag_ui_sse(evt)


async def _stale_ag_ui_generator(run: ConversationRun) -> AsyncGenerator[str, None]:
    for evt in slice_ag_ui_events_after(
        [
            stale_run_event(
                str(run.id),
                reason="run_worker_lost",
                last_event_id=run.last_event_id,
            )
        ],
        None,
        thread_id=str(run.conversation_id),
        run_id=str(run.id),
    ):
        yield format_ag_ui_sse(evt)


def _headers(run_id: str, mode: str) -> dict[str, str]:
    return {
        "X-Run-Id": run_id,
        "X-Resume-Mode": mode,
        "X-Stream-Protocol": AG_UI_PROTOCOL_HEADER,
    }


@router.get("/api/conversations/{conversation_id}/runs/{run_id}/ag-ui-stream")
async def stream_conversation_run_ag_ui(
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
            _broker_ag_ui_generator(
                broker,
                after_id,
                conversation_id=conversation_id,
                run_id=run_id,
            ),
            extra_headers=_headers(run_id_str, "live"),
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
            _stale_ag_ui_generator(run),
            extra_headers=_headers(run_id_str, "stale"),
        )

    record = await trace_storage.get_trace_by_msg_id(db, run_id_str)
    if record is None or record.conversation_id != conversation_id:
        raise resume_not_found()
    return sse_response(
        _replay_ag_ui_generator(
            record,
            after_id,
            conversation_id=conversation_id,
            run_id=run_id,
        ),
        extra_headers=_headers(run_id_str, "replay"),
    )
