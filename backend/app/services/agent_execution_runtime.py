"""Shared runtime ownership for accepted invoke and streaming executions."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

import anyio
from sqlalchemy import select
from sqlalchemy.orm import load_only, noload

from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
)
from app.authz.org_context import OrgContext
from app.database import async_session as default_async_session
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import RUN_TERMINAL_STATUSES, ConversationRun
from app.services import conversation_run_service
from app.services.conversation_run_worker import get_run_task_registry

TerminalOutcome = Literal["completed", "failed", "canceled"]
async_session = default_async_session
_CANCEL_POLL_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class RuntimeOwnershipRequest:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    root_agent_id: uuid.UUID
    context: OrgContext


@dataclass(frozen=True, slots=True)
class RuntimeOwnershipConflict(RuntimeError):
    run_id: uuid.UUID
    status: str

    def __str__(self) -> str:
        return f"Execution ownership unavailable for run {self.run_id}: {self.status}"


@dataclass(frozen=True, slots=True)
class _OwnedRuntime:
    request: RuntimeOwnershipRequest
    worker_instance_id: str


def _execution_target(request: RuntimeOwnershipRequest) -> RunExecutionTarget:
    return RunExecutionTarget(
        request.run_id,
        RunConversationTarget(
            request.conversation_id,
            request.root_agent_id,
            request.context,
        ),
    )


async def _claim(request: RuntimeOwnershipRequest) -> _OwnedRuntime:
    registry = get_run_task_registry()
    async with async_session() as db:
        await load_agent_tree_for_execution(
            db,
            AgentExecutionTarget(request.root_agent_id, request.context),
        )
        run = await lock_executable_run(db, _execution_target(request))
        if run is None or run.status != "queued":
            status = "missing" if run is None else run.status
            raise RuntimeOwnershipConflict(request.run_id, status)
        await conversation_run_service.transition_run(
            db,
            run,
            "running",
            worker_instance_id=registry.worker_instance_id,
        )
        await db.commit()
    return _OwnedRuntime(request, registry.worker_instance_id)


async def _ack_prestart_cancel(request: RuntimeOwnershipRequest) -> None:
    async with async_session() as db:
        run = await db.scalar(
            select(ConversationRun)
            .join(Conversation, Conversation.id == ConversationRun.conversation_id)
            .join(Agent, Agent.id == ConversationRun.agent_id)
            .options(
                load_only(
                    ConversationRun.id,
                    ConversationRun.conversation_id,
                    ConversationRun.agent_id,
                    ConversationRun.user_id,
                    ConversationRun.status,
                    ConversationRun.is_active,
                    ConversationRun.worker_instance_id,
                    ConversationRun.cancel_requested_at,
                    ConversationRun.cancellation_acknowledged_at,
                    ConversationRun.completed_at,
                    raiseload=True,
                ),
                noload(ConversationRun.metrics),
            )
            .where(
                ConversationRun.id == request.run_id,
                ConversationRun.conversation_id == request.conversation_id,
                ConversationRun.agent_id == request.root_agent_id,
                ConversationRun.user_id == request.context.user_id,
                ConversationRun.status == "canceling",
                ConversationRun.worker_instance_id.is_(None),
                Conversation.id == request.conversation_id,
                Conversation.agent_id == request.root_agent_id,
                Conversation.user_id == request.context.user_id,
                Conversation.org_id == request.context.org_id,
                Conversation.tenant_id == request.context.tenant_id,
                Agent.id == request.root_agent_id,
                Agent.user_id == request.context.user_id,
                Agent.org_id == request.context.org_id,
                Agent.tenant_id == request.context.tenant_id,
            )
            .with_for_update(of=ConversationRun)
            .execution_options(populate_existing=True, autoflush=False)
        )
        if run is None:
            return
        await conversation_run_service.transition_run(
            db,
            run,
            "canceled",
            allow_workerless_cancellation_ack=True,
        )
        await db.commit()


async def _poll_cancel(runtime: _OwnedRuntime) -> None:
    registry = get_run_task_registry()
    while True:
        await anyio.sleep(_CANCEL_POLL_SECONDS)
        async with async_session() as db:
            row = await db.execute(
                select(ConversationRun)
                .options(
                    load_only(
                        ConversationRun.id,
                        ConversationRun.status,
                        ConversationRun.worker_instance_id,
                        raiseload=True,
                    ),
                    noload(ConversationRun.metrics),
                )
                .where(ConversationRun.id == runtime.request.run_id)
            )
            run = row.scalar_one_or_none()
        if run is None or run.status in RUN_TERMINAL_STATUSES:
            return
        if run.status == "canceling" and run.worker_instance_id == runtime.worker_instance_id:
            registry.request_cancel(run.id, reason="stop")
            return


async def _finish(runtime: _OwnedRuntime, outcome: TerminalOutcome) -> bool:
    async with async_session() as db:
        run = await db.get(ConversationRun, runtime.request.run_id, with_for_update=True)
        if run is None or run.status in RUN_TERMINAL_STATUSES:
            return run is not None
        if run.worker_instance_id != runtime.worker_instance_id:
            return False
        if outcome == "canceled" and run.status == "running":
            await conversation_run_service.request_cancel_run(db, run, reason="stop")
        terminal: TerminalOutcome = "canceled" if run.status == "canceling" else outcome
        await conversation_run_service.transition_run(
            db,
            run,
            terminal,
            error_code="runtime_error" if terminal == "failed" else None,
            error_message="Owned execution failed" if terminal == "failed" else None,
            cancellation_ack_worker_id=(
                runtime.worker_instance_id if terminal == "canceled" else None
            ),
        )
        await db.commit()
        return True


@asynccontextmanager
async def _owned_runtime(request: RuntimeOwnershipRequest) -> AsyncIterator[None]:
    try:
        runtime = await _claim(request)
    except (AppError, RuntimeOwnershipConflict):
        await _ack_prestart_cancel(request)
        raise
    registry = get_run_task_registry()
    existing = registry.get(request.run_id)
    if existing is not None and not existing.done():
        raise RuntimeOwnershipConflict(request.run_id, "locally_owned")
    registry.start_current(request.run_id)
    try:
        failure: BaseException | None = None
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(_poll_cancel, runtime)
            try:
                yield
            except GeneratorExit as error:
                with anyio.CancelScope(shield=True):
                    await _finish(runtime, "canceled")
                failure = error
            except anyio.get_cancelled_exc_class() as error:
                with anyio.CancelScope(shield=True):
                    await _finish(runtime, "canceled")
                failure = error
            except BaseException as error:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
                with anyio.CancelScope(shield=True):
                    await _finish(runtime, "failed")
                failure = error
            else:
                if not await _finish(runtime, "completed"):
                    raise RuntimeOwnershipConflict(request.run_id, "owner_changed")
            finally:
                tasks.cancel_scope.cancel()
        if failure is not None:
            raise failure
    finally:
        registry.discard(request.run_id)


async def run_owned_execution[T](
    request: RuntimeOwnershipRequest,
    operation: Callable[[], Awaitable[T]],
) -> T:
    """Run one awaitable after durable ownership is freshly claimed."""
    async with _owned_runtime(request):
        return await operation()


async def stream_owned_execution[T](
    request: RuntimeOwnershipRequest,
    operation: Callable[[], AsyncGenerator[T, None]],
) -> AsyncGenerator[T, None]:
    """Yield a stream under the same ownership lifecycle as invoke execution."""
    async with _owned_runtime(request):
        async for item in operation():
            yield item
