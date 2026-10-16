"""Cancellation-boundary regressions for shared durable runtime ownership."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import UUID

import anyio
import pytest
from anyio.lowlevel import checkpoint

from app.models.conversation_run import ConversationRun
from app.services import agent_execution_runtime, conversation_run_worker
from app.services.agent_execution_owner import (
    CancelIntentRequest,
    notify_local_owner,
    stage_cancel_intent,
)
from app.services.agent_execution_runtime import run_owned_execution, stream_owned_execution
from app.services.conversation_run_worker import (
    CancelReason,
    RunTaskRegistry,
    get_run_task_registry,
)
from tests.authz.agent_execution_runtime_fixture import runtime_request, seed_runtime_run
from tests.conftest import TestSession


class ObservableRunTaskRegistry(RunTaskRegistry):
    """Expose shutdown delivery as a deterministic test barrier."""

    def __init__(self) -> None:
        super().__init__(worker_instance_id="shutdown-regression-worker")
        self.shutdown_requested = anyio.Event()

    def request_cancel(self, run_id: UUID, *, reason: CancelReason) -> bool:
        accepted = super().request_cancel(run_id, reason=reason)
        if reason == "shutdown":
            self.shutdown_requested.set()
        return accepted


@pytest.mark.asyncio
async def test_launch_first_cancel_is_acknowledged_only_after_operation_stops() -> None:
    run, agent, conversation = await seed_runtime_run()
    started = anyio.Event()
    stopped = anyio.Event()

    async def operation() -> None:
        started.set()
        await anyio.sleep_forever()

    async def owner() -> None:
        try:
            await run_owned_execution(runtime_request(run, agent, conversation), operation)
        except anyio.get_cancelled_exc_class():
            stopped.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(owner)
        await started.wait()
        async with TestSession() as db:
            intent = await stage_cancel_intent(db, CancelIntentRequest(run.id, "agent_archived"))
            await db.commit()
        assert notify_local_owner(intent, get_run_task_registry()) is True
        await stopped.wait()

    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "canceled"
        assert stored.cancellation_acknowledged_at is not None


@pytest.mark.asyncio
async def test_stream_aclose_after_first_event_cancels_owner_without_task_leak() -> None:
    run, agent, conversation = await seed_runtime_run()

    async def operation() -> AsyncGenerator[str, None]:
        yield "first"
        await anyio.sleep_forever()

    stream = stream_owned_execution(runtime_request(run, agent, conversation), operation)
    assert await anext(stream) == "first"
    await stream.aclose()
    await checkpoint()

    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "canceled"
        assert stored.cancel_requested_at is not None
        assert stored.worker_instance_id is not None
        assert stored.cancellation_acknowledged_at is not None
    assert get_run_task_registry().get(run.id) is None


@pytest.mark.asyncio
async def test_caller_cancel_persists_intent_then_owner_ack_and_cleans_up() -> None:
    run, agent, conversation = await seed_runtime_run()
    started = anyio.Event()

    async def operation() -> None:
        started.set()
        await anyio.sleep_forever()

    owner_scope = anyio.CancelScope()
    stopped = anyio.Event()

    async def owner() -> None:
        with owner_scope:
            try:
                await run_owned_execution(runtime_request(run, agent, conversation), operation)
            except anyio.get_cancelled_exc_class():
                stopped.set()
                raise

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(owner)
        await started.wait()
        owner_scope.cancel()
        await stopped.wait()
    await checkpoint()

    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "canceled"
        assert stored.cancel_requested_at is not None
        assert stored.cancel_reason == "stop"
        assert stored.worker_instance_id is not None
        assert stored.cancellation_acknowledged_at is not None
    assert get_run_task_registry().get(run.id) is None


@pytest.mark.asyncio
async def test_duplicate_cancel_during_terminal_persistence_does_not_interrupt_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, agent, conversation = await seed_runtime_run()
    operation_started = anyio.Event()
    finish_entered = anyio.Event()
    finish_release = anyio.Event()
    finish_completed = anyio.Event()
    owner_stopped = anyio.Event()
    original_finish = agent_execution_runtime.finish

    async def gated_finish(
        runtime: agent_execution_runtime.OwnedRuntime,
        outcome: agent_execution_runtime.TerminalOutcome,
    ) -> bool:
        finish_entered.set()
        await finish_release.wait()
        persisted = await original_finish(runtime, outcome)
        finish_completed.set()
        return persisted

    async def operation() -> None:
        operation_started.set()
        await anyio.sleep_forever()

    async def owner() -> None:
        try:
            await run_owned_execution(runtime_request(run, agent, conversation), operation)
        except anyio.get_cancelled_exc_class():
            owner_stopped.set()

    monkeypatch.setattr(agent_execution_runtime, "finish", gated_finish)
    registry = get_run_task_registry()
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(owner)
        await operation_started.wait()
        assert registry.request_cancel(run.id, reason="stop") is True
        await finish_entered.wait()
        task = registry.get(run.id)
        assert task is not None and task.cancelling() == 1
        assert registry.request_cancel(run.id, reason="stop") is True
        finish_release.set()
        await owner_stopped.wait()

    assert finish_completed.is_set()
    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "canceled"
        assert stored.cancellation_acknowledged_at is not None
    assert registry.get(run.id) is None


@pytest.mark.asyncio
async def test_shutdown_after_stop_preserves_terminal_persistence_and_last_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, agent, conversation = await seed_runtime_run()
    registry = ObservableRunTaskRegistry()
    operation_started = anyio.Event()
    finish_entered = anyio.Event()
    finish_release = anyio.Event()
    finish_completed = anyio.Event()
    owner_stopped = anyio.Event()
    shutdown_completed = anyio.Event()
    original_finish = agent_execution_runtime.finish

    async def gated_finish(
        runtime: agent_execution_runtime.OwnedRuntime,
        outcome: agent_execution_runtime.TerminalOutcome,
    ) -> bool:
        finish_entered.set()
        await finish_release.wait()
        persisted = await original_finish(runtime, outcome)
        finish_completed.set()
        return persisted

    async def operation() -> None:
        operation_started.set()
        await anyio.sleep_forever()

    async def owner() -> None:
        try:
            await run_owned_execution(runtime_request(run, agent, conversation), operation)
        except anyio.get_cancelled_exc_class():
            owner_stopped.set()

    async def shutdown() -> None:
        await registry.shutdown(timeout_seconds=1.0)
        shutdown_completed.set()

    async def skip_shutdown_persistence(_run_ids: list[UUID]) -> None:
        return None

    monkeypatch.setattr(agent_execution_runtime, "finish", gated_finish)
    monkeypatch.setattr(agent_execution_runtime, "get_run_task_registry", lambda: registry)
    monkeypatch.setattr(
        conversation_run_worker,
        "_persist_shutdown_intent",
        skip_shutdown_persistence,
    )
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(owner)
        await operation_started.wait()
        assert registry.request_cancel(run.id, reason="stop") is True
        await finish_entered.wait()
        task = registry.get(run.id)
        assert task is not None and task.cancelling() == 1
        tasks.start_soon(shutdown)
        with anyio.fail_after(1.0):
            await registry.shutdown_requested.wait()
        assert registry.cancel_reason(run.id) == "shutdown"
        assert task.cancelling() == 1
        finish_release.set()
        await owner_stopped.wait()
        await shutdown_completed.wait()

    assert finish_completed.is_set()
    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "canceled"
        assert stored.cancellation_acknowledged_at is not None
    assert registry.get(run.id) is None


@pytest.mark.asyncio
async def test_executor_base_exception_is_terminalized_before_propagation() -> None:
    run, agent, conversation = await seed_runtime_run()

    class ExecutorAbort(BaseException):
        pass

    async def operation() -> None:
        raise ExecutorAbort

    with pytest.raises(ExecutorAbort):
        await run_owned_execution(runtime_request(run, agent, conversation), operation)

    async with TestSession() as db:
        stored = await db.get(ConversationRun, run.id)
        assert stored is not None and stored.status == "failed"
        assert stored.error_code == "runtime_error"
    assert get_run_task_registry().get(run.id) is None
