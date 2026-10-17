"""Stream-resume and HiTL resume entry bodies behind ``conversation_messages`` routes."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import registry as broker_registry
from app.authz.conversation_scope import load_private_conversation
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.error_codes import conversation_not_found, resume_interrupt_pending, resume_not_found
from app.models.message_event import MessageEvent
from app.routers.chat_run_entries import _broker_resume_generator, _replay_resume_generator
from app.routers.conversation_agent_protocol_interrupts import require_allowed_rest_resume
from app.schemas.conversation import ResumeRequest
from app.services import chat_service, conversation_run_service, trace_storage
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.chat import secrets as chat_secrets
from app.services.conversation_audit_service import record_conversation_audit
from app.services.conversation_run_worker import start_conversation_run
from app.services.conversation_stream_service import resolve_agent_context, sse_response

logger = logging.getLogger("app.routers.conversation_messages")

_PROTOCOL_NON_PENDING_TERMINAL_EVENTS = frozenset(
    {"completed", "complete", "canceled", "cancelled", "failed", "error", "stale"}
)


def _messages_attr(name: str) -> Any:
    """Resolve a patchable name through the ``conversation_messages`` facade at call time."""
    from app.routers import conversation_messages

    return getattr(conversation_messages, name)


def _is_pending_interrupt(events: list[dict[str, Any]] | None) -> bool:
    if not events:
        return False
    pending = False
    for event in events:
        if event.get("event") == event_names.MESSAGE_END:
            data = event.get("data")
            status = data.get("status") if isinstance(data, Mapping) else None
            # An interrupted owner terminal closes the transport, but the
            # graph is still waiting for a resume input. Legacy message_end
            # events without an explicit status keep their historical
            # non-pending interpretation.
            if status != "interrupted":
                pending = False
            continue
        if _is_protocol_terminal_event(event):
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


async def stream_resume(
    *,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    last_event_id: str | None,
    last_event_id_header: str | None,
    db: AsyncSession,
    user: CurrentUser,
    context: OrgContext,
) -> StreamingResponse:
    after_id = last_event_id or last_event_id_header
    run_id_str = str(run_id)

    conv = await load_private_conversation(db, conversation_id, context)
    if conv is None:
        _log_resume_reject("conv_unowned_or_missing", conversation_id, run_id_str, user=user.id)
        raise resume_not_found()
    secrets = tuple(await chat_secrets.collect_conversation_secret_values(db, conv, context))

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
            _broker_resume_generator(broker, after_id, secret_values=secrets),
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
        _replay_resume_generator(
            record,
            after_id,
            mark_stale=is_stale,
            secret_values=secrets,
        ),
        extra_headers={
            "X-Run-Id": run_id_str,
            "X-Resume-Mode": "replay",
        },
    )


async def resume_message(
    *,
    conversation_id: uuid.UUID,
    data: ResumeRequest,
    request: Request,
    db: AsyncSession,
    user: CurrentUser,
    context: OrgContext,
) -> StreamingResponse:
    agent_id = await chat_service.prepare_resumed_conversation(db, conversation_id, context, data)
    conv = await chat_service.get_owned_conversation_with_agent(db, conversation_id, context)
    if conv is None:
        raise conversation_not_found()
    decisions_payload = [d.model_dump(exclude_none=True) for d in data.decisions]
    resume_payload: dict[str, Any] = {"decisions": decisions_payload}
    parent_run = await conversation_run_service.get_latest_interrupted_run(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    legacy_interrupt_trace: MessageEvent | None = None
    if parent_run is None:
        legacy_interrupt_trace = await _latest_pending_interrupt_trace(db, conversation_id)
        if legacy_interrupt_trace is None:
            raise HTTPException(
                status_code=409,
                detail="Resume run requires an interrupted parent run",
            )

    if parent_run is not None:
        interrupt_id = parent_run.interrupt_id
    else:
        if legacy_interrupt_trace is None:
            raise HTTPException(
                status_code=409,
                detail="Resume run requires an interrupted parent run",
            )
        interrupt_id = _legacy_interrupt_id(legacy_interrupt_trace)
    await require_allowed_rest_resume(db, conv, context, interrupt_id, resume_payload)
    metadata: dict[str, JsonValue] | None = None
    if legacy_interrupt_trace is not None:
        metadata = {"legacy_interrupt_assistant_msg_id": legacy_interrupt_trace.assistant_msg_id}
    cfg = await resolve_agent_context(db, conversation_id, user, context=context)
    await db.commit()
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation_id,
            root_agent_id=agent_id,
            context=context,
            source="chat",
            run_source="resume",
            input_preview=None,
            parent_run_id=parent_run.id if parent_run else None,
            interrupt_id=interrupt_id,
            run_metadata=metadata,
            allow_legacy_resume=legacy_interrupt_trace is not None,
        ),
    )
    await chat_service.touch_conversation(db, conversation_id, context)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_resume",
        conversation_id=conversation_id,
        agent_id=agent_id,
        metadata={"decision_count": len(decisions_payload)},
    )
    run_id = accepted.run_id
    await db.commit()

    ctx = await start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cfg,
        user=user,
        input_payload=resume_payload,
        moldy_source="resume",
        executor_fn=_messages_attr("resume_agent_stream"),
    )
    return sse_response(
        _broker_resume_generator(ctx.broker, None),
        extra_headers={"X-Run-Id": ctx.run_id},
    )
