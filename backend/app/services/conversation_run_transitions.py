"""Run execution target, state transitions, and heartbeat (Task 10 I4 worker split)."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any, cast

from sqlalchemy import select

from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.run_metrics_types import TerminalRunState
from app.agent_runtime.runtime_config import AgentConfig
from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
    owned_run_predicate,
)
from app.config import settings
from app.database import TenantScoped
from app.error_codes import conversation_not_found
from app.exceptions import AppError
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_service
from app.services.conversation_run_metrics_service import persist_run_metrics_and_usage
from app.services.conversation_run_task_registry import (
    _CANCEL_POLL_SECONDS,
    CancelReason,
    RunTaskRegistry,
    _session_factory,
    _tenant_session,
    worker_attr,
)

logger = logging.getLogger("app.services.conversation_run_worker")


def _effective_terminal_status(
    run: ConversationRun,
    proposed_status: TerminalRunState,
    *,
    cancellation_ack_worker_id: str | None,
    allow_workerless_cancellation_ack: bool,
) -> TerminalRunState:
    owner_ack = (
        cancellation_ack_worker_id is not None
        and run.worker_instance_id == cancellation_ack_worker_id
    )
    workerless_ack = allow_workerless_cancellation_ack and run.worker_instance_id is None
    if proposed_status != "stale" and run.status == "canceling" and (owner_ack or workerless_ack):
        return "canceled"
    return proposed_status


async def _transition(
    run_id: uuid.UUID,
    status: TerminalRunState,
    *,
    worker_instance_id: str | None = None,
    interrupt_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    cancellation_ack_worker_id: str | None = None,
    allow_workerless_cancellation_ack: bool = False,
    run_metrics: RunMetricsAccumulator,
    model_name: str,
    terminal_event_for_status: Callable[[TerminalRunState], dict[str, Any]] | None = None,
    tenant: TenantScoped,
) -> tuple[ConversationRun | None, TerminalRunState]:
    async with _tenant_session(tenant) as session:
        run = await session.get(ConversationRun, run_id, with_for_update=True)
        if run is None:
            return None, status
        effective_status = _effective_terminal_status(
            run,
            status,
            cancellation_ack_worker_id=cancellation_ack_worker_id,
            allow_workerless_cancellation_ack=allow_workerless_cancellation_ack,
        )
        try:
            metrics_snapshot = run_metrics.finalize(effective_status)
            terminal_event = (
                terminal_event_for_status(effective_status)
                if terminal_event_for_status is not None
                else None
            )
            if terminal_event is not None or effective_status == "canceled":
                await conversation_run_service.finalize_run_outputs_for_status(
                    session,
                    run,
                    effective_status,
                    append_terminal_event=terminal_event is None,
                    terminal_event=terminal_event,
                )
            await conversation_run_service.transition_run(
                session,
                run,
                effective_status,
                worker_instance_id=worker_instance_id,
                interrupt_id=interrupt_id,
                error_code=error_code,
                error_message=error_message,
                cancellation_ack_worker_id=cancellation_ack_worker_id,
                allow_workerless_cancellation_ack=allow_workerless_cancellation_ack,
            )
            await persist_run_metrics_and_usage(
                session,
                run=run,
                snapshot=metrics_snapshot,
                model_name=model_name,
            )
            await session.commit()
        except ValueError:
            await session.rollback()
            logger.exception(
                "invalid conversation run transition run_id=%s status=%s",
                run_id,
                effective_status,
            )
            raise
        return run, effective_status


def _execution_target(
    run_id: uuid.UUID, conversation_id: uuid.UUID, cfg: AgentConfig
) -> RunExecutionTarget:
    if (
        cfg.agent_id is None
        or cfg.thread_id != str(conversation_id)
        or cfg.user_id != str(cfg.org_context.user_id)
    ):
        raise conversation_not_found()
    try:
        agent_id = uuid.UUID(cfg.agent_id)
    except ValueError:
        raise conversation_not_found() from None
    return RunExecutionTarget(
        run_id, RunConversationTarget(conversation_id, agent_id, cfg.org_context)
    )


async def _transition_to_running(
    target: RunExecutionTarget,
    *,
    worker_instance_id: str,
    start: bool = True,
) -> tuple[ConversationRun | None, bool]:
    """``queued -> running`` 전이 시도. ``(run, started)`` 반환.

    cancel API 가 워커 기동 전에 ``queued -> canceling`` 을 커밋했거나 sweep 이
    먼저 terminal 로 보냈을 수 있다. 이때 무조건 전이하면 ``ValueError`` 가
    runtime failure 로 오분류되므로(canceled 가 failed 로 보임), 상태 판정과
    전이를 row lock 아래에서 함께 수행하고 시작 불가 사유는 호출자가 현재
    status 로 분기한다.
    """
    async with _tenant_session(target.conversation.context) as session:
        try:
            await load_agent_tree_for_execution(
                session,
                AgentExecutionTarget(
                    target.conversation.agent_id,
                    target.conversation.context,
                    run=True,
                ),
            )
        except AppError:
            run = await session.scalar(
                select(ConversationRun)
                .where(
                    ConversationRun.id == target.run_id,
                    ConversationRun.conversation_id == target.conversation.conversation_id,
                    ConversationRun.agent_id == target.conversation.agent_id,
                    owned_run_predicate(target.conversation.context),
                )
                .with_for_update()
                .execution_options(populate_existing=True, autoflush=False)
            )
            if run is None or not run.is_active:
                return run, False
            if run.status == "queued":
                await conversation_run_service.request_cancel_run(
                    session, run, reason="authority_revoked"
                )
                await session.commit()
            return None, False
        run = await lock_executable_run(session, target)
        if run is None:
            return None, False
        # ``start=False`` (prepared lease probe): classify without starting.
        if run.status != "queued" or not start:
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


async def _heartbeat_until_terminal(
    run_id: uuid.UUID, registry: RunTaskRegistry, tenant: TenantScoped
) -> None:
    """I-06: the run's own company session (the run belongs to its conversation's company)."""
    interval = worker_attr("_heartbeat_interval_seconds")()
    next_heartbeat = time.monotonic()
    while True:
        await asyncio.sleep(_CANCEL_POLL_SECONDS)
        async with _tenant_session(tenant) as session:
            run = await session.get(ConversationRun, run_id)
            if run is None or not run.is_active:
                # Terminalized elsewhere (archive cascade, stale sweep of another
                # worker): stop the local task; the run finalizes nothing new.
                if run is not None:
                    registry.request_cancel(run_id, reason="stop")
                return
            if run.status == "canceling":
                reason = (
                    cast(CancelReason, run.cancel_reason)
                    if run.cancel_reason in {"stop", "steer", "shutdown"}
                    else "stop"
                )
                registry.request_cancel(run_id, reason=reason)
                return
            if time.monotonic() >= next_heartbeat:
                alive = await conversation_run_service.heartbeat_run(session, run_id)
                await session.commit()
                if not alive:
                    return
                next_heartbeat = time.monotonic() + interval


async def _persist_shutdown_intent(run_ids: list[uuid.UUID]) -> None:
    """Platform by design: one shutdown write for this worker's runs of every company."""
    if not run_ids:
        return
    async with _session_factory()() as session:
        for run_id in run_ids:
            run = await session.get(ConversationRun, run_id, with_for_update=True)
            if run is not None and run.status in {"queued", "running"}:
                await conversation_run_service.request_cancel_run(
                    session,
                    run,
                    reason="shutdown",
                )
        await session.commit()
