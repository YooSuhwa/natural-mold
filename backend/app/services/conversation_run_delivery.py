"""Run event publication, audit, and post-terminal effects (Task 10 I4 worker split)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Literal

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import BrokeredEvent, EventBroker
from app.agent_runtime.protocol_redaction import REDACTED_SENSITIVE_FIELD
from app.authz.worker_conversation_scope import (
    WorkerConversationTarget,
    load_worker_conversation,
)
from app.dependencies import CurrentUser
from app.marketplace.redaction import replace_secret_values
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_service, thread_branch_service
from app.services import conversation_stream_service as stream_service
from app.services.conversation_audit_service import record_conversation_run_audit
from app.services.conversation_run_task_registry import _session_factory, worker_attr
from app.services.conversation_run_terminal_delivery import OwnerTerminalDelivery
from app.services.conversation_stream_service import StreamCtx

logger = logging.getLogger("app.services.conversation_run_worker")


def _parse_sse_chunk(chunk: str) -> list[tuple[str, str | None, dict[str, Any]]]:
    events: list[tuple[str, str | None, dict[str, Any]]] = []
    for block in chunk.split("\n\n"):
        event_name: str | None = None
        event_id: str | None = None
        data_lines: list[str] = []
        for raw_line in block.splitlines():
            if raw_line.startswith("event:"):
                event_name = raw_line[len("event:") :].strip()
            elif raw_line.startswith("id:"):
                event_id = raw_line[len("id:") :].strip()
            elif raw_line.startswith("data:"):
                data_lines.append(raw_line[len("data:") :].strip())
        if not event_name:
            continue
        raw_data = "\n".join(data_lines).strip()
        try:
            data = json.loads(raw_data) if raw_data else {}
        except json.JSONDecodeError:
            data = {"raw": raw_data}
        if isinstance(data, dict):
            events.append((event_name, event_id or None, data))
    return events


def _publish_yielded_chunk_if_needed(
    ctx: StreamCtx,
    chunk: str,
    compat_seq: int,
    emitted_events: list[dict[str, Any]] | None = None,
    *,
    broker: EventBroker | OwnerTerminalDelivery | None = None,
) -> int:
    target_broker = broker or ctx.broker
    for event_name, event_id, data in _parse_sse_chunk(chunk):
        if event_id:
            resolved_id = event_id
        else:
            compat_seq += 1
            resolved_id = f"{ctx.run_id}-compat-{compat_seq}"
        if target_broker.last_event_id == resolved_id:
            continue
        event: BrokeredEvent = {"id": resolved_id, "event": event_name, "data": data}
        target_broker.publish_nowait(event)
        if emitted_events is not None:
            emitted_events.append(dict(event))
    return compat_seq


def _redact_run_error_message(text: str, secret_values: set[str]) -> str | None:
    """실패 런의 error_message를 값 기반 마스킹 후 저장용으로 정리한다.

    run에 주입된 credential 값이 예외 텍스트에 echo되는 케이스를 exact-substring
    치환으로 가린다(ADR-021 / CLAUDE.md redaction 규칙). run-secret ContextVar는
    스트림 종료 시 이미 reset되므로 cfg.secret_values를 명시적으로 넘긴다.
    """
    masked = replace_secret_values(
        text, secret_values, placeholder=REDACTED_SENSITIVE_FIELD
    ).strip()[:1000]
    return masked or None


async def _publish_error(ctx: StreamCtx, message: str) -> None:
    compat_seq = 0
    parsed_events: list[dict[str, Any]] = []
    for chunk in stream_service.error_sse_pair(message):
        compat_seq = _publish_yielded_chunk_if_needed(
            ctx,
            chunk,
            compat_seq,
            parsed_events,
        )
    if parsed_events:
        await ctx.persist_cb(parsed_events)
        ctx.trace_sink.extend(parsed_events)


async def _publish_message_end(ctx: StreamCtx, *, status: str) -> None:
    event_id = f"{ctx.run_id}-{status}"
    # cancel 직후 CancelledError 가 다시 도착하면 같은 terminal event 를 두 번
    # publish/persist 할 수 있다 — 직전 event 와 같은 id 면 no-op.
    if ctx.broker.last_event_id == event_id:
        return
    event: BrokeredEvent = {
        "id": event_id,
        "event": event_names.MESSAGE_END,
        "data": {"usage": {}, "content": "", "status": status},
    }
    ctx.broker.publish_nowait(event)
    persisted_event: dict[str, Any] = dict(event)
    await ctx.persist_cb([persisted_event])
    ctx.trace_sink.append(persisted_event)


async def _publish_stale(ctx: StreamCtx, *, reason: str) -> None:
    event_id = f"{ctx.run_id}-stale"
    event: BrokeredEvent = {
        "id": event_id,
        "event": event_names.STALE,
        "data": {
            "reason": reason,
            "run_id": ctx.run_id,
            "last_event_id": ctx.broker.last_event_id,
        },
    }
    ctx.broker.publish_nowait(event)
    persisted_event: dict[str, Any] = dict(event)
    await ctx.persist_cb([persisted_event])
    ctx.trace_sink.append(persisted_event)


def _audit_action_for_terminal_status(status: conversation_run_service.RunStatus) -> str | None:
    if status == "completed":
        return "conversation.run_complete"
    if status == "canceled":
        return "conversation.run_canceled"
    if status == "interrupted":
        return "conversation.run_interrupted"
    if status == "stale":
        return "conversation.run_stale"
    if status == "failed":
        return "conversation.run_failed"
    return None


async def _record_run_audit(
    *,
    action: str,
    run: ConversationRun,
    user: CurrentUser,
    status: str | None = None,
) -> None:
    async with _session_factory()() as session:
        await record_conversation_run_audit(
            session,
            action=action,
            run=run,
            user=user,
            status=status,
        )
        await session.commit()


async def _activate_latest_branch_leaf_if_needed(
    *,
    target: WorkerConversationTarget,
    moldy_source: str,
    final_status: conversation_run_service.RunStatus,
) -> None:
    if final_status != "completed" or moldy_source not in {"edit", "regenerate"}:
        return
    async with _session_factory()() as session:
        conversation = await load_worker_conversation(session, target)
        if conversation is None:
            return
        try:
            tree = await thread_branch_service.build_message_tree(
                worker_attr("get_checkpointer")(),
                str(target.conversation_id),
                active_checkpoint_id=None,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "failed to resolve latest branch leaf after %s run conversation_id=%s",
                moldy_source,
                target.conversation_id,
                exc_info=True,
            )
            return
        if not tree.active_checkpoint_id:
            return
        conversation = await load_worker_conversation(session, target)
        if conversation is None:
            return
        conversation.active_branch_checkpoint_id = tree.active_checkpoint_id
        await session.commit()


def _trace_status_for_run(
    status: conversation_run_service.RunStatus,
) -> Literal["completed", "failed"]:
    if status in {"completed", "interrupted", "canceled"}:
        return "completed"
    return "failed"


async def _backfill_turn_attachments(
    target: WorkerConversationTarget, attachment_ids: list[uuid.UUID]
) -> None:
    """Stamp this send's uploads with the turn's user message id (M1).

    Runs once at finalize, after the turn's HumanMessage is in the checkpoint,
    in its own session so it can't poison the run-teardown transaction.
    """

    from app.services import chat_service

    async with _session_factory()() as session:
        conversation = await load_worker_conversation(session, target)
        if conversation is None:
            return
        message_id = await chat_service.resolve_turn_user_message_id(session, conversation)
        if not message_id:
            return
        await chat_service.link_attachments_to_message(
            session,
            attachment_ids=attachment_ids,
            message_id=message_id,
            conversation_id=conversation.id,
            context=target.context,
        )
        await session.commit()
