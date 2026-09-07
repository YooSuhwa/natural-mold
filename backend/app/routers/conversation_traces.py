from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.dependencies import CurrentUser, get_current_user, get_db, owned_conversation
from app.error_codes import (
    trace_not_found,
)
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.observability.langfuse import is_langfuse_enabled
from app.schemas.conversation import (
    DebugTraceDetailResponse,
    DebugTraceListResponse,
    TurnTraceResponse,
)
from app.schemas.conversation_run_message_links import (
    ConversationRunMessageKey,
    ConversationRunMessageLinkResponse,
)
from app.services import trace_debug_service, trace_storage
from app.services.chat import secrets as chat_secrets
from app.services.conversation_run_message_links import RunMessageLinkQuery, list_run_message_links

router = APIRouter(tags=["conversations"])


@router.get(
    "/api/conversations/{conversation_id}/run-message-links",
    response_model=list[ConversationRunMessageLinkResponse],
)
async def get_conversation_run_message_links(
    conversation_id: uuid.UUID,
    message_id: Annotated[list[ConversationRunMessageKey], Query(min_length=1, max_length=50)],
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _conversation: Conversation = Depends(owned_conversation),
) -> list[ConversationRunMessageLinkResponse]:
    return await list_run_message_links(
        db,
        RunMessageLinkQuery(
            conversation_id=conversation_id,
            user_id=user.id,
            message_ids=message_id,
        ),
    )


def _public_turn_trace(
    record: MessageEvent,
    *,
    secret_values: Sequence[str] | None = None,
) -> TurnTraceResponse:
    """Copy a persisted trace into its browser-safe representation."""
    return TurnTraceResponse(
        assistant_msg_id=record.assistant_msg_id,
        events=project_and_redact_protocol_data(
            "traces",
            record.events or [],
            secret_values=secret_values,
        ),
        last_event_id=record.last_event_id,
        linked_message_ids=record.linked_message_ids,
        created_at=record.created_at,
        completed_at=record.completed_at,
    )


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
)
async def list_traces(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    conversation: Conversation = Depends(owned_conversation),
):
    records = await trace_storage.get_traces_for_conversation(db, conversation_id)
    secrets = tuple(await chat_secrets.collect_conversation_secret_values(db, conversation))
    return [_public_turn_trace(record, secret_values=secrets) for record in records]


@router.get(
    "/api/conversations/{conversation_id}/debug/traces",
    response_model=DebugTraceListResponse,
)
async def list_debug_traces(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    conversation: Conversation = Depends(owned_conversation),
):
    records = await trace_storage.get_traces_for_conversation(db, conversation_id)
    secrets = tuple(await chat_secrets.collect_conversation_secret_values(db, conversation))
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
                secret_values=secrets,
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
    conversation: Conversation = Depends(owned_conversation),
):
    records = await trace_storage.get_traces_for_conversation(db, conversation_id)
    record = next(
        (item for item in records if trace_id in {item.external_trace_id, item.assistant_msg_id}),
        None,
    )
    if record is None:
        raise trace_not_found()

    run_statuses = await _run_status_by_message_event_id(db, [record])
    secrets = tuple(await chat_secrets.collect_conversation_secret_values(db, conversation))
    summary, spans, raw, fallback_reason = await trace_debug_service.build_debug_detail(
        record,
        run_status=run_statuses.get(record.assistant_msg_id),
        secret_values=secrets,
    )
    return DebugTraceDetailResponse(
        conversation_id=conversation_id,
        trace=summary,
        spans=spans,
        raw=raw,
        fallback_reason=fallback_reason,
    )
