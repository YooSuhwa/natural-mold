from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any, Literal

from app.agent_runtime import event_names
from app.agent_runtime.checkpointer import get_checkpointer
from app.agent_runtime.event_broker import BrokeredEvent
from app.agent_runtime.runtime_config import AgentConfig
from app.config import settings
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_service, thread_branch_service
from app.services import conversation_stream_service as stream_service
from app.services.conversation_audit_service import record_conversation_run_audit
from app.services.conversation_run_interrupts import (
    has_interrupt_events,
    interrupt_id_from_events,
)
from app.services.conversation_stream_service import StreamCtx

logger = logging.getLogger(__name__)

AgentStreamExecutor = Callable[..., AsyncGenerator[str, None]]
async_session = None
CancelReason = Literal["user", "shutdown"]


def _session_factory():
    return async_session or stream_service.async_session


class RunTaskRegistry:
    def __init__(self, *, worker_instance_id: str | None = None) -> None:
        self.worker_instance_id = worker_instance_id or f"worker-{uuid.uuid4().hex[:16]}"
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._cancel_reasons: dict[uuid.UUID, CancelReason] = {}

    def start(self, run_id: uuid.UUID, task: asyncio.Task[None]) -> None:
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            raise RuntimeError(f"run task already exists: {run_id}")
        self._tasks[run_id] = task

        def _discard(done_task: asyncio.Task[None]) -> None:
            self.discard(run_id)
            with contextlib.suppress(asyncio.CancelledError):
                exc = done_task.exception()
                if exc is not None:
                    logger.error(
                        "conversation run task crashed run_id=%s",
                        run_id,
                        exc_info=(type(exc), exc, exc.__traceback__),
                    )

        task.add_done_callback(_discard)

    def get(self, run_id: uuid.UUID) -> asyncio.Task[None] | None:
        return self._tasks.get(run_id)

    def active_run_ids(self) -> set[uuid.UUID]:
        return {run_id for run_id, task in self._tasks.items() if not task.done()}

    def cancel(self, run_id: uuid.UUID) -> bool:
        return self.request_cancel(run_id, reason="user")

    def request_cancel(self, run_id: uuid.UUID, *, reason: CancelReason) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        self._cancel_reasons[run_id] = reason
        task.cancel()
        return True

    def cancel_reason(self, run_id: uuid.UUID) -> CancelReason | None:
        return self._cancel_reasons.get(run_id)

    def discard(self, run_id: uuid.UUID) -> None:
        self._tasks.pop(run_id, None)
        self._cancel_reasons.pop(run_id, None)

    async def shutdown(self, timeout_seconds: float = 10.0) -> None:
        items = [(run_id, task) for run_id, task in self._tasks.items() if not task.done()]
        for run_id, task in items:
            self._cancel_reasons[run_id] = "shutdown"
            task.cancel()
        tasks = [task for _, task in items]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "timed out waiting for %d conversation run task(s) during shutdown",
                    len(tasks),
                )
        self._tasks.clear()
        self._cancel_reasons.clear()


_registry = RunTaskRegistry()


def get_run_task_registry() -> RunTaskRegistry:
    return _registry


def reset_run_task_registry_for_tests(registry: RunTaskRegistry | None = None) -> None:
    global _registry
    _registry = registry or RunTaskRegistry()


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
) -> int:
    for event_name, event_id, data in _parse_sse_chunk(chunk):
        if event_id:
            resolved_id = event_id
        else:
            compat_seq += 1
            resolved_id = f"{ctx.run_id}-compat-{compat_seq}"
        if ctx.broker.last_event_id == resolved_id:
            continue
        event: BrokeredEvent = {"id": resolved_id, "event": event_name, "data": data}
        ctx.broker.publish_nowait(event)
        if emitted_events is not None:
            emitted_events.append(event)
    return compat_seq


async def start_conversation_run(
    *,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    cfg: AgentConfig,
    user: CurrentUser,
    input_payload: Any,
    moldy_source: str,
    executor_fn: AgentStreamExecutor,
    registry: RunTaskRegistry | None = None,
    attachment_ids: list[uuid.UUID] | None = None,
) -> StreamCtx:
    registry = registry or get_run_task_registry()
    ctx = stream_service.prepare_stream_context(conversation_id, run_id=str(run_id))
    task = asyncio.create_task(
        _run_conversation(
            run_id=run_id,
            conversation_id=conversation_id,
            cfg=cfg,
            user=user,
            input_payload=input_payload,
            moldy_source=moldy_source,
            executor_fn=executor_fn,
            ctx=ctx,
            registry=registry,
            attachment_ids=attachment_ids,
        ),
        name=f"conversation-run-{run_id}",
    )
    registry.start(run_id, task)
    return ctx


async def _transition(
    run_id: uuid.UUID,
    status: conversation_run_service.RunStatus,
    *,
    worker_instance_id: str | None = None,
    interrupt_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ConversationRun | None:
    async with _session_factory()() as session:
        run = await session.get(ConversationRun, run_id, with_for_update=True)
        if run is None:
            return None
        try:
            await conversation_run_service.transition_run(
                session,
                run,
                status,
                worker_instance_id=worker_instance_id,
                interrupt_id=interrupt_id,
                error_code=error_code,
                error_message=error_message,
            )
            await session.commit()
        except ValueError:
            await session.rollback()
            logger.exception(
                "invalid conversation run transition run_id=%s status=%s",
                run_id,
                status,
            )
            raise
        return run


async def _transition_to_running(
    run_id: uuid.UUID,
    *,
    worker_instance_id: str,
) -> tuple[ConversationRun | None, bool]:
    """``queued -> running`` 전이 시도. ``(run, started)`` 반환.

    cancel API 가 워커 기동 전에 ``queued -> canceling`` 을 커밋했거나 sweep 이
    먼저 terminal 로 보냈을 수 있다. 이때 무조건 전이하면 ``ValueError`` 가
    runtime failure 로 오분류되므로(canceled 가 failed 로 보임), 상태 판정과
    전이를 row lock 아래에서 함께 수행하고 시작 불가 사유는 호출자가 현재
    status 로 분기한다.
    """
    async with _session_factory()() as session:
        run = await session.get(ConversationRun, run_id, with_for_update=True)
        if run is None:
            return None, False
        if run.status != "queued":
            return run, False
        await conversation_run_service.transition_run(
            session,
            run,
            "running",
            worker_instance_id=worker_instance_id,
        )
        await session.commit()
        return run, True


def _heartbeat_interval_seconds() -> float:
    return max(1.0, min(30.0, settings.chat_run_stale_after_seconds / 3))


async def _heartbeat_until_terminal(run_id: uuid.UUID) -> None:
    interval = _heartbeat_interval_seconds()
    while True:
        await asyncio.sleep(interval)
        async with _session_factory()() as session:
            alive = await conversation_run_service.heartbeat_run(session, run_id)
            await session.commit()
            if not alive:
                return


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
    await ctx.persist_cb([event])
    ctx.trace_sink.append(event)


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
    await ctx.persist_cb([event])
    ctx.trace_sink.append(event)


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
    conversation_id: uuid.UUID,
    moldy_source: str,
    final_status: conversation_run_service.RunStatus,
) -> None:
    if final_status != "completed" or moldy_source not in {"edit", "regenerate"}:
        return
    try:
        tree = await thread_branch_service.build_message_tree(
            get_checkpointer(),
            str(conversation_id),
            active_checkpoint_id=None,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "failed to resolve latest branch leaf after %s run conversation_id=%s",
            moldy_source,
            conversation_id,
            exc_info=True,
        )
        return
    if not tree.active_checkpoint_id:
        return

    async with _session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id, with_for_update=True)
        if conversation is None:
            return
        conversation.active_branch_checkpoint_id = tree.active_checkpoint_id
        await session.commit()


def _trace_status_for_run(status: conversation_run_service.RunStatus) -> str:
    if status in {"completed", "interrupted", "canceled"}:
        return "completed"
    return "failed"


async def _backfill_turn_attachments(
    conversation_id: uuid.UUID, attachment_ids: list[uuid.UUID]
) -> None:
    """Stamp this send's uploads with the turn's user message id (M1).

    Runs once at finalize, after the turn's HumanMessage is in the checkpoint,
    in its own session so it can't poison the run-teardown transaction.
    """

    from app.services import chat_service

    async with _session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            return
        message_id = await chat_service.resolve_turn_user_message_id(session, conversation)
        if not message_id:
            return
        await chat_service.link_attachments_to_message(
            session, attachment_ids=attachment_ids, message_id=message_id
        )
        await session.commit()


async def _run_conversation(
    *,
    run_id: uuid.UUID,
    conversation_id: uuid.UUID,
    cfg: AgentConfig,
    user: CurrentUser,
    input_payload: Any,
    moldy_source: str,
    executor_fn: AgentStreamExecutor,
    ctx: StreamCtx,
    registry: RunTaskRegistry,
    attachment_ids: list[uuid.UUID] | None = None,
) -> None:
    final_status: conversation_run_service.RunStatus = "completed"
    failure: Exception | None = None
    error_code: str | None = None
    error_message: str | None = None
    interrupt_id: str | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    compat_seq = 0
    # 워커가 run 을 시작하지 못한 경우(소멸/이미 terminal)에는 finally 의
    # trace finalize + terminal 전이를 건너뛴다 — 다른 경로가 이미 끝낸 run 을
    # 다시 finalize 하지 않기 위함.
    finalize_needed = True
    try:
        run, started = await _transition_to_running(
            run_id,
            worker_instance_id=registry.worker_instance_id,
        )
        if run is None:
            logger.warning("conversation run vanished before worker start run_id=%s", run_id)
            finalize_needed = False
            return
        if not started:
            if run.status == "canceling":
                # Stop 요청이 워커 기동 전(queued)에 도착 — 실행 없이 canceled 로
                # 종료한다. running 전이 실패를 failed 로 오분류하지 않는다.
                final_status = "canceled"
                await _publish_message_end(ctx, status="canceled")
            else:
                logger.warning(
                    "conversation run not startable run_id=%s status=%s",
                    run_id,
                    run.status,
                )
                finalize_needed = False
            return
        await _record_run_audit(
            action="conversation.run_start",
            run=run,
            user=user,
            status="running",
        )
        heartbeat_task = asyncio.create_task(
            _heartbeat_until_terminal(run_id),
            name=f"conversation-run-heartbeat-{run_id}",
        )

        stream_kwargs = ctx.as_stream_kwargs()
        stream_kwargs["artifact_recorder"] = stream_service.build_artifact_recorder(
            conversation_id=conversation_id,
            cfg=cfg,
            user=user,
            run_id=ctx.run_id,
        )
        async for chunk in executor_fn(
            cfg,
            input_payload,
            moldy_source=moldy_source,
            **stream_kwargs,
        ):
            compat_seq = _publish_yielded_chunk_if_needed(ctx, chunk, compat_seq)

        if ctx.has_stream_error():
            final_status = "failed"
        elif has_interrupt_events(ctx.trace_sink):
            final_status = "interrupted"
            interrupt_id = interrupt_id_from_events(ctx.trace_sink)
    except asyncio.CancelledError:
        cancel_reason = registry.cancel_reason(run_id)
        if cancel_reason == "shutdown":
            final_status = "stale"
            error_code = "worker_shutdown"
            error_message = "Application shutdown canceled the run before completion."
            await _publish_stale(ctx, reason="worker_shutdown")
        else:
            final_status = "canceled"
            await _publish_message_end(ctx, status="canceled")
    except Exception as exc:
        final_status = "failed"
        failure = exc
        error_code = "runtime_error"
        error_message = str(exc)[:1000]
        logger.exception("conversation run worker failed run_id=%s", run_id)
        await _publish_error(ctx, "에이전트 실행 중 오류가 발생했습니다.")
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

        if finalize_needed:
            try:
                await stream_service.finalize_trace(
                    conversation_id,
                    ctx.run_id,
                    ctx.trace_sink,
                    ctx.msg_id_sink,
                    ctx.langfuse_sink,
                    success=final_status == "completed",
                    status=_trace_status_for_run(final_status),
                    run_status=final_status,
                )
            except Exception:
                logger.exception("conversation run trace finalization failed run_id=%s", run_id)

            try:
                final_run = await _transition(
                    run_id,
                    final_status,
                    error_code=error_code,
                    error_message=error_message,
                    interrupt_id=interrupt_id,
                )
                action = _audit_action_for_terminal_status(final_status)
                if final_run is not None and action is not None:
                    await _record_run_audit(
                        action=action,
                        run=final_run,
                        user=user,
                        status=final_status,
                    )
                await _activate_latest_branch_leaf_if_needed(
                    conversation_id=conversation_id,
                    moldy_source=moldy_source,
                    final_status=final_status,
                )
            except Exception:
                logger.exception("conversation run status finalization failed run_id=%s", run_id)

            if attachment_ids:
                # M1 — stamp this send's uploads with the user message id the read
                # path will compute. Runs AFTER branch activation so it resolves
                # against the SAME active_branch_checkpoint_id the read path uses
                # (an edit/regenerate run forks a new leaf that's activated just
                # above; resolving before activation would walk the stale branch
                # and mis-link). Best-effort: a failure leaves message_id NULL
                # (orphan GC reaps it later) rather than breaking run teardown.
                try:
                    await _backfill_turn_attachments(conversation_id, attachment_ids)
                except Exception:
                    logger.exception("attachment message_id backfill failed run_id=%s", run_id)

        ctx.broker.close(error=failure)
        registry.discard(run_id)


__all__ = [
    "RunTaskRegistry",
    "get_run_task_registry",
    "reset_run_task_registry_for_tests",
    "start_conversation_run",
]
