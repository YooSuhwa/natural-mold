"""Shared runtime ownership for accepted invoke and streaming executions."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from app.agent_runtime.prepared_run_inputs import MaterializerSeams
from app.agent_runtime.prepared_run_transaction import (
    PrepareInputs,
    Schedule,
    materialize_and_activate,
)
from app.agent_runtime.runtime_config import PreparedRunLease
from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.conversation_run_scope import lock_executable_run
from app.database import async_session as default_async_session
from app.database import tenant_session, tenant_sessions
from app.exceptions import AppError
from app.models.conversation_run import RUN_TERMINAL_STATUSES, ConversationRun
from app.services import conversation_run_service
from app.services.agent_execution_lifecycle import (
    OwnedRuntime,
    RuntimeOwnershipConflict,
    RuntimeOwnershipRequest,
    TerminalOutcome,
    execution_target,
    supervise_owned_run,
)
from app.services.agent_execution_supervision import ack_prestart_cancel
from app.services.conversation_run_worker import get_run_task_registry
from app.services.prepared_run_state import fail_prepared_run, mark_prepared_running

async_session = default_async_session
_CANCEL_POLL_SECONDS = 0.1


async def _claim(request: RuntimeOwnershipRequest) -> OwnedRuntime:
    registry = get_run_task_registry()
    async with tenant_session(request.context, maker=async_session) as db:
        await load_agent_tree_for_execution(
            db,
            AgentExecutionTarget(request.root_agent_id, request.context, run=True),
        )
        run = await lock_executable_run(db, execution_target(request))
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
    return OwnedRuntime(request, registry.worker_instance_id)


async def finish(runtime: OwnedRuntime, outcome: TerminalOutcome) -> bool:
    """Write the owner's terminal state; ``False`` when another worker owns the row."""
    runtime.flags.finishing = True  # our own terminal write must not trip the poll
    async with tenant_session(runtime.request.context, maker=async_session) as db:
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
        await ack_prestart_cancel(request)
        raise
    async with supervise_owned_run(runtime):
        yield


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


async def materialize_prepared_execution(
    request: RuntimeOwnershipRequest,
    *,
    prepare_inputs: PrepareInputs,
    schedule: Schedule,
    seams: MaterializerSeams | None = None,
) -> PreparedRunLease:
    """I15 lease entry (chat/queue/API). A failure after activation (e.g. a
    cancellation at the session close) never leaks the ACTIVE lease (I24 H4)."""
    from app.services.prepared_lease_cleanup import abandon_activated_lease

    activated: list[PreparedRunLease] = []

    async def scheduled(lease: PreparedRunLease) -> None:
        activated.append(lease)
        await schedule(lease)

    try:
        async with tenant_session(request.context, maker=async_session) as db:
            return await materialize_and_activate(
                db,
                execution_target(request),
                prepare_inputs=prepare_inputs,
                mark_running=mark_prepared_running(get_run_task_registry().worker_instance_id),
                fail_running=fail_prepared_run,
                schedule=scheduled,
                session_factory=tenant_sessions(request.context, maker=async_session),
                seams=seams,
            )
    except BaseException:
        if activated:  # idempotent if the transaction already closed it
            await abandon_activated_lease(execution_target(request), activated[0].digest)
        raise
