"""Recheck the owning conversation before admitting one provider attempt."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial

from sqlalchemy import select

from app.agent_runtime.stream_retry_authority import StreamAttemptDenied, bind_stream_attempt_check
from app.authz.conversation_run_scope import (
    RunExecutionTarget,
    lock_executable_run,
    private_run_predicate,
)
from app.database import tenant_session
from app.models.conversation_run import ConversationRun
from app.services.conversation_run_fence import RunOwnershipLost, current_run_fence, lock_run_fence
from app.services.conversation_run_task_registry import RunTaskRegistry


@dataclass(frozen=True, slots=True)
class _Owner:
    target: RunExecutionTarget
    registry: RunTaskRegistry
    task: asyncio.Task[None] | None


@contextmanager
def stream_attempt_owner(target: RunExecutionTarget, registry: RunTaskRegistry) -> Iterator[None]:
    """Bind only at the entry point that has claimed and registered this run."""
    owner = _Owner(target, registry, registry.get(target.run_id))
    with bind_stream_attempt_check(partial(_check_owner, owner)):
        yield


def _check_local(owner: _Owner) -> None:
    task = owner.task
    if task is None or task.done() or owner.registry.get(owner.target.run_id) is not task:
        raise StreamAttemptDenied()
    if task.cancelling() or owner.registry.cancel_reason(owner.target.run_id):
        raise asyncio.CancelledError


async def _check_owner(owner: _Owner, prepared: bool) -> None:
    """Release all database locks before sending.

    A prepared sender also opens its own dispatch permit on every attempt; that
    permit determines whether its exact materialized lease can use an archived
    organization. The additional query checks only its canonical run owner.
    """
    _check_local(owner)
    target = owner.target
    conversation = target.conversation
    async with tenant_session(conversation.context) as db:
        if prepared:
            run = await db.scalar(
                select(ConversationRun).where(
                    ConversationRun.id == target.run_id,
                    ConversationRun.conversation_id == conversation.conversation_id,
                    ConversationRun.agent_id == conversation.agent_id,
                    private_run_predicate(conversation.context),
                )
            )
        else:
            run = await lock_executable_run(db, target)
        if run is None or run.worker_instance_id != owner.registry.worker_instance_id:
            raise StreamAttemptDenied()
        if run.cancel_requested_at is not None or run.status == "canceling":
            raise asyncio.CancelledError
        if not run.is_active or run.status != "running":
            raise StreamAttemptDenied()
        fence = current_run_fence()
        if fence is not None:
            if (
                fence.run_id != target.run_id
                or fence.conversation_id != conversation.conversation_id
            ):
                raise RunOwnershipLost("Provider attempt belongs to another chat run")
            await lock_run_fence(db, fence)
    _check_local(owner)
