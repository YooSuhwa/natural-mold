"""Chat run entry bodies behind the thin ``conversation_messages`` routes."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator, Iterable
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import EventBroker, slice_events_after
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.streaming import format_sse
from app.authz.conversation_agent_checks import require_scoped_agent_permission
from app.authz.conversation_scope import require_conversation_agent_id
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.error_codes import conversation_not_found
from app.models.message_event import MessageEvent
from app.schemas.conversation import MessageCreate
from app.schemas.conversation_scope import ConversationAutoTitle
from app.services import chat_service
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_audit_service import record_conversation_audit
from app.services.conversation_run_worker import start_conversation_run
from app.services.conversation_stream_service import resolve_agent_context, sse_response

logger = logging.getLogger("app.routers.conversation_messages")


def _messages_attr(name: str) -> Any:
    """Resolve a patchable name through the ``conversation_messages`` facade at call time."""
    from app.routers import conversation_messages

    return getattr(conversation_messages, name)


async def start_conversation_with_message(
    *,
    agent_id: uuid.UUID,
    data: MessageCreate,
    request: Request,
    db: AsyncSession,
    user: CurrentUser,
    context: OrgContext,
) -> StreamingResponse:
    title = chat_service.conversation_title_from_content(data.content)
    await require_scoped_agent_permission(db, context, agent_id, "can_run")
    conv = await chat_service.create_conversation(db, agent_id, title, context=context)
    # Config hydration performs its own lifecycle check and therefore starts from
    # a clean transaction. The final acceptance below rechecks after hydration.
    await db.commit()
    await chat_service.prepare_executable_conversation(db, conv.id, context, agent_checked=True)
    cfg = await resolve_agent_context(db, conv.id, user, context=context)
    await db.commit()
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conv.id,
            root_agent_id=agent_id,
            context=context,
            source="chat",
            run_source="start",
            input_preview=data.content,
        ),
    )
    await chat_service.touch_conversation(db, conv.id, context)

    if data.attachments:
        await chat_service.link_attachments_to_conversation(
            db,
            conversation_id=conv.id,
            context=context,
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
        agent_id=require_conversation_agent_id(conv),
        title=conv.title,
        metadata={
            "content_length": len(data.content),
            "attachment_count": len(data.attachments or []),
        },
    )
    run_id = accepted.run_id
    await db.commit()

    ctx = await start_conversation_run(
        run_id=run_id,
        conversation_id=conv.id,
        cfg=cfg,
        user=user,
        input_payload=[{"role": "user", "content": data.content}],
        moldy_source="chat",
        executor_fn=_messages_attr("execute_agent_stream"),
        attachment_ids=[a.id for a in data.attachments] if data.attachments else None,
    )
    return sse_response(
        _broker_resume_generator(ctx.broker, None),
        extra_headers={
            "X-Run-Id": ctx.run_id,
            "X-Conversation-Id": str(conv.id),
        },
    )


def _normalize_event_id(raw: object) -> str | None:
    return raw if isinstance(raw, str) and raw else None


async def _broker_resume_generator(
    broker: EventBroker,
    after_id: str | None,
    *,
    secret_values: Iterable[str] | None = None,
) -> AsyncGenerator[str, None]:
    async for evt in broker.subscribe(after_id=after_id):
        yield format_sse(
            evt["event"],
            project_and_redact_protocol_data(
                evt["event"], evt["data"], secret_values=secret_values
            ),
            event_id=_normalize_event_id(evt.get("id")),
        )


async def _replay_resume_generator(
    record: MessageEvent,
    after_id: str | None,
    *,
    mark_stale: bool,
    secret_values: Iterable[str] | None = None,
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
        yield format_sse(
            evt_name,
            project_and_redact_protocol_data(
                evt_name,
                evt.get("data") or {},
                secret_values=secret_values,
            ),
            event_id=emitted_id,
        )
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


async def send_message(
    *,
    conversation_id: uuid.UUID,
    data: MessageCreate,
    request: Request,
    db: AsyncSession,
    user: CurrentUser,
    context: OrgContext,
) -> StreamingResponse:
    agent_id = await chat_service.prepare_executable_conversation(db, conversation_id, context)
    conv = await chat_service.get_owned_conversation_with_agent(db, conversation_id, context)
    if conv is None:
        raise conversation_not_found()
    cfg = await resolve_agent_context(db, conversation_id, user, context=context)
    await db.commit()
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation_id,
            root_agent_id=agent_id,
            context=context,
            source="chat",
            input_preview=data.content,
        ),
    )
    await chat_service.maybe_set_auto_title(
        db, ConversationAutoTitle(conversation_id, context, data.content)
    )
    await chat_service.touch_conversation(db, conversation_id, context)

    if data.attachments:
        await chat_service.link_attachments_to_conversation(
            db,
            conversation_id=conversation_id,
            context=context,
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
    run_id = accepted.run_id
    await db.commit()

    ctx = await start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cfg,
        user=user,
        input_payload=[{"role": "user", "content": data.content}],
        moldy_source="chat",
        executor_fn=_messages_attr("execute_agent_stream"),
        attachment_ids=[a.id for a in data.attachments] if data.attachments else None,
    )
    return sse_response(
        _broker_resume_generator(ctx.broker, None, secret_values=cfg.secret_values),
        extra_headers={"X-Run-Id": ctx.run_id},
    )
