from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator, Mapping
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import EventBroker, slice_events_after
from app.agent_runtime.event_broker import registry as broker_registry
from app.agent_runtime.streaming import format_sse
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import (
    agent_not_found,
    conversation_not_found,
    resume_interrupt_pending,
    resume_not_found,
)
from app.models.message_event import MessageEvent
from app.schemas.conversation import MessageCreate, MessagesEnvelope, ResumeRequest
from app.schemas.conversation_run import ConversationRunResponse
from app.services import (
    chat_service,
    conversation_run_service,
    thread_branch_service,
    trace_storage,
)
from app.services.conversation_audit_service import record_conversation_audit
from app.services.conversation_run_worker import start_conversation_run
from app.services.conversation_stream_service import (
    execute_agent_stream,
    resolve_agent_context,
    resume_agent_stream,
    sse_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])
_PROTOCOL_NON_PENDING_TERMINAL_EVENTS = frozenset(
    {"completed", "complete", "canceled", "cancelled", "failed", "error", "stale"}
)


def _cfg_agent_uuid(cfg: Any):
    if not cfg.agent_id:
        raise agent_not_found()
    return uuid.UUID(cfg.agent_id)


@router.post("/api/agents/{agent_id}/conversations/start")
async def start_conversation_with_message(
    agent_id: uuid.UUID,
    data: MessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await chat_service.get_agent_with_tools(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()

    title = chat_service.conversation_title_from_content(data.content)
    conv = await chat_service.create_conversation(db, agent_id, title)
    cfg = await resolve_agent_context(db, conv.id, user)
    await chat_service.touch_conversation(db, conv.id)

    if data.attachments:
        await chat_service.link_attachments_to_conversation(
            db,
            conversation_id=conv.id,
            user_id=user.id,
            attachment_ids=[a.id for a in data.attachments],
        )

    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.create",
        conversation_id=conv.id,
        agent_id=agent_id,
        title=conv.title,
    )
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_send",
        conversation_id=conv.id,
        agent_id=uuid.UUID(cfg.agent_id) if cfg.agent_id else None,
        title=conv.title,
        metadata={
            "content_length": len(data.content),
            "attachment_count": len(data.attachments or []),
        },
    )
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conv.id,
        agent_id=agent_id,
        user_id=user.id,
        source="start",
        input_preview=data.content,
    )
    run_id = run.id
    await db.commit()

    ctx = await start_conversation_run(
        run_id=run_id,
        conversation_id=conv.id,
        cfg=cfg,
        user=user,
        input_payload=[{"role": "user", "content": data.content}],
        moldy_source="chat",
        executor_fn=execute_agent_stream,
    )
    return sse_response(
        _broker_resume_generator(ctx.broker, None),
        extra_headers={
            "X-Run-Id": ctx.run_id,
            "X-Conversation-Id": str(conv.id),
        },
    )


@router.get(
    "/api/conversations/{conversation_id}/messages",
    response_model=MessagesEnvelope,
)
async def list_messages(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()
    active_run = await conversation_run_service.current_run_for_conversation(
        db,
        conversation_id=conversation_id,
    )
    active_run_response = (
        ConversationRunResponse.model_validate(active_run) if active_run is not None else None
    )
    latest_run = await conversation_run_service.latest_run_for_conversation(
        db,
        conversation_id=conversation_id,
    )
    latest_run_response = (
        ConversationRunResponse.model_validate(latest_run) if latest_run is not None else None
    )

    from app.agent_runtime.checkpointer import get_checkpointer

    tree = None
    try:
        checkpointer = get_checkpointer()
        tree = await thread_branch_service.build_message_tree(
            checkpointer,
            str(conversation_id),
            active_checkpoint_id=conv.active_branch_checkpoint_id,
        )
    except RuntimeError:
        tree = None

    messages = []
    if tree is not None:
        messages = await chat_service.list_messages_from_checkpointer(
            db, conv, user_id=user.id, tree=tree
        )

    total_cost = sum(
        (m.usage.estimated_cost or 0.0)
        for m in messages
        if m.usage and m.usage.estimated_cost is not None
    )

    if tree is None:
        return MessagesEnvelope(
            messages=messages,
            active_run=active_run_response,
            latest_run=latest_run_response,
            total_estimated_cost=total_cost,
        )

    active_tip: uuid.UUID | None = None
    if tree.active_tip_message_id:
        from app.agent_runtime.message_utils import parse_msg_id

        active_tip = parse_msg_id(tree.active_tip_message_id, conversation_id, 0)
    return MessagesEnvelope(
        messages=messages,
        active_run=active_run_response,
        latest_run=latest_run_response,
        active_tip_message_id=active_tip,
        active_checkpoint_id=conv.active_branch_checkpoint_id or tree.active_checkpoint_id,
        total_estimated_cost=total_cost,
    )


def _is_pending_interrupt(events: list[dict[str, Any]] | None) -> bool:
    if not events:
        return False
    pending = False
    for event in events:
        if event.get("event") == event_names.MESSAGE_END or _is_protocol_terminal_event(event):
            pending = False
            continue
        if event.get("event") == event_names.INTERRUPT or event.get("method") == "input.requested":
            pending = True
    return pending


def _is_protocol_terminal_event(event: dict[str, Any]) -> bool:
    if event.get("method") != "lifecycle":
        return False
    data = event.get("data")
    if not isinstance(data, Mapping):
        return False
    status = data.get("event") or data.get("status")
    return status in _PROTOCOL_NON_PENDING_TERMINAL_EVENTS


async def _latest_pending_interrupt_trace(
    db: AsyncSession,
    conversation_id: uuid.UUID,
) -> MessageEvent | None:
    records = await trace_storage.get_traces_for_conversation(db, conversation_id)
    for record in reversed(records):
        if _is_pending_interrupt(record.events):
            return record
    return None


def _legacy_interrupt_id(record: MessageEvent) -> str:
    return f"legacy:{record.assistant_msg_id}"


def _normalize_event_id(raw: object) -> str | None:
    return raw if isinstance(raw, str) and raw else None


async def _broker_resume_generator(
    broker: EventBroker, after_id: str | None
) -> AsyncGenerator[str, None]:
    async for evt in broker.subscribe(after_id=after_id):
        yield format_sse(evt["event"], evt["data"], event_id=_normalize_event_id(evt.get("id")))


async def _replay_resume_generator(
    record: MessageEvent,
    after_id: str | None,
    *,
    mark_stale: bool,
) -> AsyncGenerator[str, None]:
    last_emitted_id: str | None = None
    for evt in slice_events_after(record.events or [], after_id):
        evt_name = evt.get("event")
        if not isinstance(evt_name, str) or not evt_name:
            logger.warning(
                "stream_resume skip corrupt evt msg_id=%s evt_id=%s",
                record.assistant_msg_id,
                evt.get("id"),
            )
            continue
        emitted_id = _normalize_event_id(evt.get("id"))
        yield format_sse(evt_name, evt.get("data") or {}, event_id=emitted_id)
        if emitted_id:
            last_emitted_id = emitted_id

    if mark_stale:
        stale_id = record.last_event_id or last_emitted_id
        yield format_sse(
            event_names.STALE,
            {
                "reason": "broker_lost" if stale_id else "broker_lost_no_id",
                "last_event_id": stale_id,
            },
        )


def _log_resume_reject(
    reason: str,
    conversation_id: uuid.UUID,
    run_id_str: str,
    **extra: Any,
) -> None:
    suffix = " " + " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
    logger.info(
        "stream_resume reject reason=%s conv=%s run_id=%s%s",
        reason,
        conversation_id,
        run_id_str,
        suffix,
    )


@router.get("/api/conversations/{conversation_id}/stream")
async def stream_resume(
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    last_event_id: str | None = Query(None),
    last_event_id_header: str | None = Header(None, alias="Last-Event-ID"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    after_id = last_event_id or last_event_id_header
    run_id_str = str(run_id)

    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if conv is None:
        _log_resume_reject("conv_unowned_or_missing", conversation_id, run_id_str, user=user.id)
        raise resume_not_found()

    broker = broker_registry.get(run_id_str)
    if broker is not None and not broker.is_closed:
        if broker.conversation_id != str(conversation_id):
            _log_resume_reject(
                "broker_conv_mismatch",
                conversation_id,
                run_id_str,
                broker_conv=broker.conversation_id,
            )
            raise resume_not_found()
        logger.info(
            "stream_resume mode=live conv=%s run_id=%s after_id=%s",
            conversation_id,
            run_id_str,
            after_id,
        )
        return sse_response(
            _broker_resume_generator(broker, after_id),
            extra_headers={
                "X-Run-Id": run_id_str,
                "X-Resume-Mode": "live",
            },
        )

    record = await trace_storage.get_trace_by_msg_id(db, run_id_str)
    if record is None:
        _log_resume_reject("row_missing", conversation_id, run_id_str)
        raise resume_not_found()
    if record.conversation_id != conversation_id:
        _log_resume_reject(
            "row_conv_mismatch",
            conversation_id,
            run_id_str,
            row_conv=record.conversation_id,
        )
        raise resume_not_found()
    if _is_pending_interrupt(record.events):
        _log_resume_reject("interrupt_pending", conversation_id, run_id_str)
        raise resume_interrupt_pending()

    is_stale = record.status == "streaming"
    if is_stale:
        logger.warning(
            "stream_resume stale conv=%s run_id=%s last_event_id=%s "
            "(broker lost while turn streaming)",
            conversation_id,
            run_id_str,
            record.last_event_id,
        )
    logger.info(
        "stream_resume mode=replay conv=%s run_id=%s after_id=%s status=%s",
        conversation_id,
        run_id_str,
        after_id,
        record.status,
    )
    return sse_response(
        _replay_resume_generator(record, after_id, mark_stale=is_stale),
        extra_headers={
            "X-Run-Id": run_id_str,
            "X-Resume-Mode": "replay",
        },
    )


@router.post("/api/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    data: MessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    cfg = await resolve_agent_context(db, conversation_id, user)
    await chat_service.maybe_set_auto_title(db, conversation_id, data.content)
    await chat_service.touch_conversation(db, conversation_id)

    if data.attachments:
        await chat_service.link_attachments_to_conversation(
            db,
            conversation_id=conversation_id,
            user_id=user.id,
            attachment_ids=[a.id for a in data.attachments],
        )
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_send",
        conversation_id=conversation_id,
        agent_id=uuid.UUID(cfg.agent_id) if cfg.agent_id else None,
        metadata={
            "content_length": len(data.content),
            "attachment_count": len(data.attachments or []),
        },
    )
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=_cfg_agent_uuid(cfg),
        user_id=user.id,
        source="chat",
        input_preview=data.content,
    )
    run_id = run.id
    await db.commit()

    ctx = await start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cfg,
        user=user,
        input_payload=[{"role": "user", "content": data.content}],
        moldy_source="chat",
        executor_fn=execute_agent_stream,
    )
    return sse_response(
        _broker_resume_generator(ctx.broker, None),
        extra_headers={"X-Run-Id": ctx.run_id},
    )


@router.post("/api/conversations/{conversation_id}/messages/resume")
async def resume_message(
    conversation_id: uuid.UUID,
    data: ResumeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    cfg = await resolve_agent_context(db, conversation_id, user)
    await chat_service.touch_conversation(db, conversation_id)

    decisions_payload: list[dict[str, Any]] = [
        d.model_dump(exclude_none=True) for d in data.decisions
    ]
    resume_payload: dict[str, Any] = {"decisions": decisions_payload}
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_resume",
        conversation_id=conversation_id,
        agent_id=uuid.UUID(cfg.agent_id) if cfg.agent_id else None,
        metadata={"decision_count": len(decisions_payload)},
    )
    parent_run = await conversation_run_service.get_latest_interrupted_run(
        db,
        conversation_id=conversation_id,
        user_id=user.id,
    )
    legacy_interrupt_trace: MessageEvent | None = None
    if parent_run is None:
        legacy_interrupt_trace = await _latest_pending_interrupt_trace(db, conversation_id)
        if legacy_interrupt_trace is None:
            raise HTTPException(
                status_code=409,
                detail="Resume run requires an interrupted parent run",
            )

    interrupt_id = (
        parent_run.interrupt_id if parent_run else _legacy_interrupt_id(legacy_interrupt_trace)
    )
    metadata = None
    if legacy_interrupt_trace is not None:
        metadata = {"legacy_interrupt_assistant_msg_id": legacy_interrupt_trace.assistant_msg_id}
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=_cfg_agent_uuid(cfg),
        user_id=user.id,
        source="resume",
        input_preview=None,
        parent_run_id=parent_run.id if parent_run else None,
        interrupt_id=interrupt_id,
        metadata=metadata,
        allow_legacy_resume=legacy_interrupt_trace is not None,
    )
    run_id = run.id
    await db.commit()

    ctx = await start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cfg,
        user=user,
        input_payload=resume_payload,
        moldy_source="resume",
        executor_fn=resume_agent_stream,
    )
    return sse_response(
        _broker_resume_generator(ctx.broker, None),
        extra_headers={"X-Run-Id": ctx.run_id},
    )
