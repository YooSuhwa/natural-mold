"""Fence writes and calls made by a recoverable chat execution generation."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime

from langgraph.errors import GraphBubbleUp
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.authz.conversation_run_scope import owned_run_predicate
from app.authz.org_context import OrgContext
from app.database import async_session, tenant_session
from app.models.conversation_run import RUN_TERMINAL_STATUSES, ConversationRun, utc_now_naive


class RunOwnershipLost(GraphBubbleUp, RuntimeError):
    """A worker can no longer act on behalf of this running generation."""


@dataclass(frozen=True, slots=True)
class RunFence:
    run_id: uuid.UUID
    worker_instance_id: str
    generation: int
    owner_token: uuid.UUID
    tenant: OrgContext
    conversation_id: uuid.UUID
    started_at: datetime | None = None


_current: ContextVar[RunFence | None] = ContextVar("conversation_run_fence", default=None)


def open_run_session(fence: RunFence) -> AsyncSession:
    """Open a transaction in the claimed conversation's original tenant."""
    return tenant_session(fence.tenant, maker=async_session)


def current_run_fence() -> RunFence | None:
    return _current.get()


@contextmanager
def run_fence_scope(fence: RunFence | None) -> Iterator[None]:
    token = _current.set(fence)
    try:
        yield
    finally:
        _current.reset(token)


def fence_for_run(run: ConversationRun) -> RunFence:
    if not (
        run.owner_token
        and run.worker_instance_id
        and run.owner_generation > 0
        and run.org_id
        and run.tenant_id
    ):
        raise RunOwnershipLost("Missing chat run ownership")
    return RunFence(
        run.id,
        run.worker_instance_id,
        run.owner_generation,
        run.owner_token,
        OrgContext(org_id=run.org_id, tenant_id=run.tenant_id, user_id=run.user_id),
        run.conversation_id,
        run.started_at,
    )


def claim_run_owner(run: ConversationRun, worker_instance_id: str) -> RunFence:
    """Rotate the capability under the caller's locked, authorized run transaction."""
    if run.status != "running" or not run.is_active or not run.org_id or not run.tenant_id:
        raise RunOwnershipLost("Chat run cannot be claimed")
    run.owner_generation += 1
    run.owner_token = uuid.uuid4()
    run.worker_instance_id = worker_instance_id
    run.heartbeat_at = utc_now_naive()
    return fence_for_run(run)


def run_fence_statement(
    fence: RunFence, *, allow_canceling: bool = False, allow_terminal: bool = False
) -> Select[tuple[ConversationRun]]:
    statuses = ["running"]
    if allow_canceling:
        statuses.append("canceling")
    if allow_terminal:
        statuses.extend(RUN_TERMINAL_STATUSES)
    statement = (
        select(ConversationRun)
        .where(
            ConversationRun.id == fence.run_id,
            ConversationRun.conversation_id == fence.conversation_id,
            ConversationRun.tenant_id == fence.tenant.tenant_id,
            ConversationRun.org_id == fence.tenant.org_id,
            owned_run_predicate(fence.tenant),
            ConversationRun.worker_instance_id == fence.worker_instance_id,
            ConversationRun.owner_token == fence.owner_token,
            ConversationRun.owner_generation == fence.generation,
            ConversationRun.status.in_(statuses),
        )
        .options(noload(ConversationRun.metrics))
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if not allow_terminal:
        statement = statement.where(ConversationRun.is_active.is_(True))
    return statement


async def lock_run_fence(
    db: AsyncSession,
    fence: RunFence,
    *,
    allow_canceling: bool = False,
    allow_terminal: bool = False,
) -> ConversationRun:
    row = await db.scalar(
        run_fence_statement(fence, allow_canceling=allow_canceling, allow_terminal=allow_terminal)
    )
    if row is None:
        raise RunOwnershipLost("Chat run ownership has changed")
    return row


async def lock_current_output(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    run_id: str | uuid.UUID,
    allow_canceling: bool = False,
    allow_terminal: bool = False,
) -> ConversationRun | None:
    """Hold the publishing generation's row lock through its output transaction."""
    fence = current_run_fence()
    if fence is None:
        return None
    if str(fence.run_id) != str(run_id) or fence.conversation_id != conversation_id:
        raise RunOwnershipLost("Output belongs to another chat run")
    return await lock_run_fence(
        db, fence, allow_canceling=allow_canceling, allow_terminal=allow_terminal
    )
