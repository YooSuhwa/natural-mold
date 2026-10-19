"""Shared owned-run supervision for the legacy, Agent API and trigger entries.

One ``async with supervise_owned_run(...)`` wraps the body of an already
claimed (or materialization-marked) run: it registers the current task, runs the
cancel poll beside the body, and writes the terminal ``ConversationRun`` state.
Outcomes are stored and raised *after* the task group closes, so a
``RuntimeOwnershipConflict`` (or the body's own error) is never wrapped in an
anyio 4 ``ExceptionGroup``.

``finish`` and the task registry are read call-time from
``app.services.agent_execution_runtime`` (tests patch that facade).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio

from app.authz.conversation_run_scope import RunConversationTarget, RunExecutionTarget
from app.authz.org_context import OrgContext
from app.services.agent_execution_supervision import OwnerFlags, poll_cancel

TerminalOutcome = Literal["completed", "failed", "canceled"]
FailureHandler = Callable[["OwnedRuntime", BaseException], Awaitable[None]]


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
class OwnedRuntime:
    request: RuntimeOwnershipRequest
    worker_instance_id: str
    flags: OwnerFlags = field(default_factory=OwnerFlags, compare=False)


def execution_target(request: RuntimeOwnershipRequest) -> RunExecutionTarget:
    conversation = RunConversationTarget(
        request.conversation_id, request.root_agent_id, request.context
    )
    return RunExecutionTarget(request.run_id, conversation)


def _owned() -> Any:
    from app.services import agent_execution_runtime

    return agent_execution_runtime


async def _fail(runtime: OwnedRuntime, _error: BaseException) -> None:
    await _owned().finish(runtime, "failed")


@asynccontextmanager
async def supervise_owned_run(
    runtime: OwnedRuntime,
    *,
    heartbeat: bool = False,
    on_failure: FailureHandler = _fail,
    on_locally_owned: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[None]:
    """Supervise one owned run; re-raise the body's failure or the ownership conflict."""
    owned = _owned()
    registry = owned.get_run_task_registry()
    run_id = runtime.request.run_id
    existing = registry.get(run_id)
    if existing is not None and not existing.done():
        if on_locally_owned is not None:
            await on_locally_owned()
        raise RuntimeOwnershipConflict(run_id, "locally_owned")
    registry.start_current(run_id)
    failure: BaseException | None = None
    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(poll_cancel, runtime, heartbeat)
            try:
                yield
            except (GeneratorExit, anyio.get_cancelled_exc_class()) as error:
                with anyio.CancelScope(shield=True):
                    await owned.finish(runtime, "canceled")
                failure = error
            except BaseException as error:  # noqa: BLE001
                with anyio.CancelScope(shield=True):
                    await on_failure(runtime, error)
                failure = error
            else:
                if not await owned.finish(runtime, "completed"):
                    failure = RuntimeOwnershipConflict(run_id, "owner_changed")
            finally:
                tasks.cancel_scope.cancel()
        if failure is not None:
            raise failure
    finally:
        registry.discard(run_id)


__all__ = [
    "OwnedRuntime",
    "RuntimeOwnershipConflict",
    "RuntimeOwnershipRequest",
    "TerminalOutcome",
    "execution_target",
    "supervise_owned_run",
]
