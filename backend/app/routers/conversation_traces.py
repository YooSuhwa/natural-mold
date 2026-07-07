from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, owned_conversation
from app.error_codes import (
    trace_not_found,
)
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.observability.langfuse import is_langfuse_enabled
from app.schemas.conversation import (
    DebugTraceDetailResponse,
    DebugTraceListResponse,
    TurnTraceResponse,
)
from app.services import trace_debug_service, trace_storage

router = APIRouter(tags=["conversations"])


async def _run_status_by_message_event_id(
    db: AsyncSession,
    records: Sequence[MessageEvent],
) -> dict[str, str]:
    run_ids: list[uuid.UUID] = []
    for record in records:
        try:
            run_ids.append(uuid.UUID(str(record.assistant_msg_id)))
        except (TypeError, ValueError):
            continue
    if not run_ids:
        return {}
    rows = await db.execute(
        select(ConversationRun.id, ConversationRun.status).where(ConversationRun.id.in_(run_ids))
    )
    return {str(run_id): status for run_id, status in rows.all()}


@router.get(
    "/api/conversations/{conversation_id}/traces",
    response_model=list[TurnTraceResponse],
    dependencies=[Depends(owned_conversation)],
)
async def list_traces(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await trace_storage.get_traces_for_conversation(db, conversation_id)


@router.get(
    "/api/conversations/{conversation_id}/debug/traces",
    response_model=DebugTraceListResponse,
    dependencies=[Depends(owned_conversation)],
)
async def list_debug_traces(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    records = await trace_storage.get_traces_for_conversation(db, conversation_id)
    run_statuses = await _run_status_by_message_event_id(db, records)
    langfuse_enabled = is_langfuse_enabled()
    fallback_reason = None if langfuse_enabled else "Langfuse disabled"
    return DebugTraceListResponse(
        conversation_id=conversation_id,
        langfuse_enabled=langfuse_enabled,
        fallback_reason=fallback_reason,
        traces=[
            trace_debug_service.summary_from_record(
                record,
                fallback_reason=(fallback_reason if not record.external_trace_id else None),
                run_status=run_statuses.get(record.assistant_msg_id),
            )
            for record in records
        ],
    )


@router.get(
    "/api/conversations/{conversation_id}/debug/traces/{trace_id}",
    response_model=DebugTraceDetailResponse,
    dependencies=[Depends(owned_conversation)],
)
async def get_debug_trace_detail(
    conversation_id: uuid.UUID,
    trace_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    records = await trace_storage.get_traces_for_conversation(db, conversation_id)
    record = next(
        (item for item in records if trace_id in {item.external_trace_id, item.assistant_msg_id}),
        None,
    )
    if record is None:
        raise trace_not_found()

    run_statuses = await _run_status_by_message_event_id(db, [record])
    summary, spans, raw, fallback_reason = await trace_debug_service.build_debug_detail(
        record,
        run_status=run_statuses.get(record.assistant_msg_id),
    )
    return DebugTraceDetailResponse(
        conversation_id=conversation_id,
        trace=summary,
        spans=spans,
        raw=raw,
        fallback_reason=fallback_reason,
    )
