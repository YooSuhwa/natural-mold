"""Owned conversation run execution body (Task 10 I4 worker split)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any

from app.agent_runtime.run_error_codes import run_error_code
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.run_metrics_types import TerminalRunState
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.stream_error_messages import public_stream_error_message
from app.authz.worker_conversation_scope import WorkerConversationTarget
from app.dependencies import CurrentUser
from app.services import conversation_stream_service as stream_service
from app.services.conversation_run_delivery import (
    _audit_action_for_terminal_status,
    _publish_error,
    _publish_message_end,
    _publish_stale,
    _publish_yielded_chunk_if_needed,
    _record_run_audit,
    _redact_run_error_message,
    _trace_status_for_run,
)
from app.services.conversation_run_task_registry import RunTaskRegistry, worker_attr
from app.services.conversation_run_terminal_delivery import OwnerTerminalDelivery
from app.services.conversation_run_transitions import _execution_target, _transition_to_running
from app.services.conversation_stream_service import StreamCtx

logger = logging.getLogger("app.services.conversation_run_worker")

AgentStreamExecutor = Callable[..., AsyncGenerator[str, None]]


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
    config_transform: Callable[[AgentConfig], AgentConfig] | None = None,
) -> None:
    from app.services import prepared_conversation_start as prepared_start

    metrics_started_at = time.monotonic()
    run_metrics = RunMetricsAccumulator(started_at=metrics_started_at)
    final_status: TerminalRunState = "completed"
    failure: Exception | None = None
    error_code: str | None = None
    error_message: str | None = None
    interrupt_id: str | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    compat_seq = 0
    # 워커가 run 을 시작하지 못한 경우(소멸/이미 terminal)에는 finally 의
    # trace finalize + terminal 전이를 건너뛴다 — 다른 경로가 이미 끝낸 run 을
    # 다시 finalize 하지 않기 위함.
    finalize_needed = False
    workerless_cancel_before_start = False
    executor_exhausted_normally = False
    terminal_delivery = OwnerTerminalDelivery(
        run_id=ctx.run_id,
        broker=ctx.broker,
        persist_callback=ctx.persist_cb,
        trace_sink=ctx.trace_sink,
    )
    start: prepared_start.PreparedRunStart | None = None
    try:
        start = await prepared_start.start_owned_run(
            _execution_target(run_id, conversation_id, cfg),
            prepared_start.OwnedRunInputs(cfg, user, input_payload, executor_fn, config_transform),
            worker_instance_id=registry.worker_instance_id,
            started_at=metrics_started_at,
            transition=_transition_to_running,
        )
        run, started = start.run, start.started
        cfg = start.cfg or cfg  # prepared: locked metadata + the lease's secret set
        if run is None:
            logger.warning("conversation run vanished before worker start run_id=%s", run_id)
            finalize_needed = False
            return
        finalize_needed = True
        if not started:
            if run.status == "canceling" and run.worker_instance_id is None:
                # Stop 요청이 워커 기동 전(queued)에 도착 — 실행 없이 canceled 로
                # 종료한다. running 전이 실패를 failed 로 오분류하지 않는다.
                final_status = "canceled"
                workerless_cancel_before_start = True
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
            worker_attr("_heartbeat_until_terminal")(run_id, registry),
            name=f"conversation-run-heartbeat-{run_id}",
        )
        run_metrics = start.run_metrics or await worker_attr("_prepare_run_metrics")(
            conversation_id=conversation_id,
            cfg=cfg,
            started_at=metrics_started_at,
        )

        stream_kwargs = ctx.as_stream_kwargs()
        stream_kwargs["broker"] = terminal_delivery
        stream_kwargs["persist_callback"] = terminal_delivery.persist
        stream_kwargs["run_metrics"] = run_metrics
        stream_kwargs["artifact_recorder"] = stream_service.build_artifact_recorder(
            conversation_id=conversation_id,
            cfg=cfg,
            user=user,
            run_id=ctx.run_id,
            output_dir=prepared_start.lease_output_dir(start),  # M1: index the lease output
        )
        async for chunk in (start.executor or executor_fn)(
            cfg,
            input_payload,
            moldy_source=moldy_source,
            **stream_kwargs,
        ):
            compat_seq = _publish_yielded_chunk_if_needed(
                ctx,
                chunk,
                compat_seq,
                broker=terminal_delivery,
            )
        executor_exhausted_normally = True

        if ctx.has_stream_error():
            final_status = "failed"
            # 스트림 도중 실패는 예외로 전파되지 않고 error_sink에 기록된다
            # (streaming.py에서 public_stream_error_message로 1차 블록리스트 마스킹).
            # run에 주입된 credential 값 기반 마스킹을 한 번 더 적용해 채팅 에러
            # 버블이 폴백 대신 구체적 원인을 안전하게 보이게 한다(G2 retry).
            # _run_metadata는 failed일 때만 이를 노출한다.
            error_code = run_error_code(ctx.error_sink[0].error, "stream_error")
            error_message = _redact_run_error_message(
                ctx.error_sink[0].message or "", cfg.secret_values
            )
        elif worker_attr("has_interrupt_events")(ctx.trace_sink):
            final_status = "interrupted"
            interrupt_id = worker_attr("interrupt_id_from_events")(ctx.trace_sink)
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
        # A failed prepared start left the row queued; finalize fails it (I15).
        finalize_needed = finalize_needed or isinstance(exc, prepared_start.PreparedStartError)
        final_status = "failed"
        failure = exc
        error_code = run_error_code(exc, "runtime_error")  # shared code table (M2)
        # 스트림 바깥 예외(credential 해석/checkpointer/DB 등)는 어떤 마스킹도
        # 거치지 않았다. public_stream_error_message(블록리스트) + 값 기반
        # (cfg.secret_values) 2단 마스킹으로 주입된 credential 값 노출을 막는다.
        # 단 파일경로/DB호스트 등 내부 토폴로지는 이 2단으로 가려지지 않으므로
        # runtime_error의 error_message는 채팅 버블에 노출하지 않는다(_run_metadata가
        # stream_error만 노출). 마스킹된 상세는 GET /runs/{id} 운영 경로에만 남고,
        # 원본 예외는 서버 로그(logger.exception)에만 남는다.
        error_message = _redact_run_error_message(
            public_stream_error_message(exc), cfg.secret_values
        )
        logger.exception("conversation run worker failed run_id=%s", run_id)
        await _publish_error(ctx, "에이전트 실행 중 오류가 발생했습니다.")
    finally:
        # An activated lease the stream never claimed is closed (idempotent).
        await prepared_start.release_unclaimed(start)
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

        if executor_exhausted_normally:
            terminal_delivery.remove_staged_terminal_from_trace()

        async def _finalize_trace_for_status(
            effective_status: TerminalRunState,
        ) -> None:
            try:
                await stream_service.finalize_trace(
                    conversation_id,
                    ctx.run_id,
                    ctx.trace_sink,
                    ctx.msg_id_sink,
                    ctx.langfuse_sink,
                    success=effective_status == "completed",
                    status=_trace_status_for_run(effective_status),
                    run_status=effective_status,
                )
            except Exception:
                logger.exception("conversation run trace finalization failed run_id=%s", run_id)

        async def _finalize_run_status() -> TerminalRunState:
            try:
                final_run, effective_status = await worker_attr("_transition")(
                    run_id,
                    final_status,
                    error_code=error_code,
                    error_message=error_message,
                    interrupt_id=interrupt_id,
                    cancellation_ack_worker_id=registry.worker_instance_id,
                    allow_workerless_cancellation_ack=workerless_cancel_before_start,
                    run_metrics=run_metrics,
                    model_name=getattr(cfg, "model_name", "unknown"),
                    terminal_event_for_status=(
                        lambda status: terminal_delivery.terminal_events_for_status(status)[1]
                    )
                    if executor_exhausted_normally
                    else None,
                )
                if final_run is not None and executor_exhausted_normally:
                    live_terminal, persisted_terminal = (
                        terminal_delivery.terminal_events_for_status(effective_status)
                    )
                    terminal_delivery.publish_terminal(live_terminal, persisted_terminal)
                action = _audit_action_for_terminal_status(effective_status)
                if final_run is not None and action is not None:
                    await _record_run_audit(
                        action=action,
                        run=final_run,
                        user=user,
                        status=effective_status,
                    )
                await worker_attr("_activate_latest_branch_leaf_if_needed")(
                    target=WorkerConversationTarget(conversation_id, cfg.org_context),
                    moldy_source=moldy_source,
                    final_status=effective_status,
                )
            except Exception:
                logger.exception("conversation run status finalization failed run_id=%s", run_id)
                return final_status
            return effective_status

        if finalize_needed:
            effective_status = final_status
            # Interrupted runs still commit before trace finalization so resume can
            # find the parent. Other runs keep trace first; a late cancellation is
            # reconciled inside the subsequent locked transition before queue dispatch.
            if final_status == "interrupted":
                effective_status = await _finalize_run_status()

            await _finalize_trace_for_status(effective_status)

            if final_status != "interrupted":
                await _finalize_run_status()

            if attachment_ids:
                # M1 — stamp this send's uploads with the user message id the read
                # path will compute. Runs AFTER branch activation so it resolves
                # against the SAME active_branch_checkpoint_id the read path uses
                # (an edit/regenerate run forks a new leaf that's activated just
                # above; resolving before activation would walk the stale branch
                # and mis-link). Best-effort: a failure leaves message_id NULL
                # (orphan GC reaps it later) rather than breaking run teardown.
                try:
                    await worker_attr("_backfill_turn_attachments")(
                        WorkerConversationTarget(conversation_id, cfg.org_context), attachment_ids
                    )
                except Exception:
                    logger.exception("attachment message_id backfill failed run_id=%s", run_id)

        await prepared_start.remove_lease_run_root(start)  # after terminalization (M1)
        ctx.broker.close(error=failure)
        registry.discard(run_id)
        if finalize_needed:
            from app.services.conversation_run_queue_worker import (
                dispatch_next_for_conversation,
            )

            await dispatch_next_for_conversation(conversation_id)
