from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import conversation_not_found
from app.observability.langfuse import is_langfuse_enabled
from app.schemas.conversation import (
    DebugTraceDetailResponse,
    DebugTraceListResponse,
    TurnTraceResponse,
)
from app.services import chat_service, trace_debug_service, trace_storage

router = APIRouter(tags=["conversations"])


@router.get(
    "/api/conversations/{conversation_id}/traces",
    response_model=list[TurnTraceResponse],
)
async def list_traces(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()
    return await trace_storage.get_traces_for_conversation(db, conversation_id)


@router.get(
    "/api/conversations/{conversation_id}/debug/traces",
    response_model=DebugTraceListResponse,
)
async def list_debug_traces(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()

    records = await trace_storage.get_traces_for_conversation(db, conversation_id)
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
            )
            for record in records
        ],
    )


@router.get(
    "/api/conversations/{conversation_id}/debug/traces/{trace_id}",
    response_model=DebugTraceDetailResponse,
)
async def get_debug_trace_detail(
    conversation_id: uuid.UUID,
    trace_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()

    records = await trace_storage.get_traces_for_conversation(db, conversation_id)
    record = next(
        (item for item in records if trace_id in {item.external_trace_id, item.assistant_msg_id}),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Trace not found")

    summary, spans, raw, fallback_reason = await trace_debug_service.build_debug_detail(record)
    return DebugTraceDetailResponse(
        conversation_id=conversation_id,
        trace=summary,
        spans=spans,
        raw=raw,
        fallback_reason=fallback_reason,
    )
