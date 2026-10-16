"""Enumerate and lock one organization's archive scope in canonical order (internal I22).

The caller already holds ``Tenant -> Organization`` ``FOR UPDATE`` (which blocks
every gated Agent/Conversation/run creator). This module then follows the shared
execution order, skipping member classes: sorted Agent lifecycle locks, active
``AgentTrigger``, active ``AgentDeployment``, Conversations with an active run
or a pending input, their active runs, their pending inputs plus inputs claimed by
those locked runs (``claimed`` is also the permanent state of consumed inputs),
then linked and unlinked live API/trigger envelopes of the org. Every
repeatable class is admitted through :class:`ExecutionLockCursor`, so an
out-of-order or non-ascending acquisition fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.execution_lock_cursor import ExecutionLockCursor, ExecutionProofError, LockClass
from app.models.agent import Agent
from app.models.agent_api import AgentApiRun, AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import RUN_ACTIVE_STATUSES, ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.organization import Organization
from app.services import agent_lifecycle_locks

_API_TERMINAL = ("succeeded", "failed", "canceled")
_TRIGGER_TERMINAL = ("success", "failed", "canceled", "skipped")


@dataclass(frozen=True, slots=True)
class ArchiveScope:
    agent_ids: tuple[UUID, ...]
    triggers: tuple[AgentTrigger, ...]
    deployments: tuple[AgentDeployment, ...]
    conversation_ids: tuple[UUID, ...]
    runs: tuple[ConversationRun, ...]
    inputs: tuple[ConversationRunInput, ...]
    api_runs: tuple[AgentApiRun, ...]
    trigger_runs: tuple[AgentTriggerRun, ...]
    unlinked_api_runs: tuple[AgentApiRun, ...]
    unlinked_trigger_runs: tuple[AgentTriggerRun, ...]
    lock_trace: tuple[tuple[LockClass, UUID], ...]


@dataclass(slots=True)
class _Locker:
    db: AsyncSession
    cursor: ExecutionLockCursor
    trace: list[tuple[LockClass, UUID]]

    def held(self, lock_class: LockClass, resource_id: UUID) -> None:
        self.cursor.admit(lock_class, resource_id)
        self.cursor.record(lock_class, resource_id)
        self.trace.append((lock_class, resource_id))

    async def rows(self, lock_class: LockClass, statement: Select[tuple[Any]]) -> list[Any]:
        rows = list(
            (
                await self.db.scalars(
                    statement.with_for_update().execution_options(populate_existing=True)
                )
            ).all()
        )
        for row in rows:
            self.held(lock_class, row.id)
        return rows


async def _agent_ids(db: AsyncSession, organization: Organization) -> tuple[UUID, ...]:
    ids = await db.scalars(
        select(Agent.id).where(
            Agent.org_id == organization.id, Agent.tenant_id == organization.tenant_id
        )
    )
    return tuple(sorted(ids, key=lambda value: value.bytes))


async def _live_conversation_ids(db: AsyncSession, organization: Organization) -> list[UUID]:
    active_run = select(ConversationRun.conversation_id).where(
        ConversationRun.status.in_(RUN_ACTIVE_STATUSES)
    )
    live_input = select(ConversationRunInput.conversation_id).where(
        ConversationRunInput.status == "pending"
    )
    ids = await db.scalars(
        select(Conversation.id).where(
            Conversation.org_id == organization.id,
            Conversation.tenant_id == organization.tenant_id,
            or_(Conversation.id.in_(active_run), Conversation.id.in_(live_input)),
        )
    )
    return list(ids)


async def lock_archive_scope(db: AsyncSession, organization: Organization) -> ArchiveScope:
    """Lock every archive-affected row after the held Tenant/Organization locks."""
    locker = _Locker(db, ExecutionLockCursor(), [])
    locker.held(LockClass.TENANT, organization.tenant_id)
    locker.held(LockClass.ORGANIZATION, organization.id)
    with db.no_autoflush:
        agent_ids = await _agent_ids(db, organization)
        for agent_id in agent_ids:
            locker.held(LockClass.AGENT_LIFECYCLE, agent_id)
        await agent_lifecycle_locks.acquire_agent_lifecycle_locks(db, agent_ids)
        if await _agent_ids(db, organization) != agent_ids:
            # Gated creators wait on the Organization lock; drift means an ungated writer.
            raise ExecutionProofError("agent_tree_changed")
        scoped = (AgentTrigger.org_id == organization.id, AgentTrigger.status == "active")
        triggers = await locker.rows(
            LockClass.TRIGGER, select(AgentTrigger).where(*scoped).order_by(AgentTrigger.id)
        )
        deployments = await locker.rows(
            LockClass.DEPLOYMENT,
            select(AgentDeployment)
            .where(AgentDeployment.org_id == organization.id, AgentDeployment.status == "active")
            .order_by(AgentDeployment.id),
        )
        candidate_ids = await _live_conversation_ids(db, organization)
        conversations = await locker.rows(
            LockClass.CONVERSATION,
            select(Conversation)
            .where(Conversation.id.in_(candidate_ids))
            .order_by(Conversation.id),
        )
        conversation_ids = tuple(row.id for row in conversations)
        runs = await locker.rows(
            LockClass.RUN,
            select(ConversationRun)
            .where(
                ConversationRun.conversation_id.in_(conversation_ids),
                ConversationRun.status.in_(RUN_ACTIVE_STATUSES),
            )
            .order_by(ConversationRun.id),
        )
        run_ids = tuple(row.id for row in runs)
        inputs = await locker.rows(
            LockClass.INPUT,
            select(ConversationRunInput)
            .where(
                ConversationRunInput.conversation_id.in_(conversation_ids),
                or_(
                    ConversationRunInput.status == "pending",
                    (ConversationRunInput.status == "claimed")
                    & ConversationRunInput.run_id.in_(run_ids),
                ),
            )
            .order_by(ConversationRunInput.id),
        )
        org_deployments = select(AgentDeployment.id).where(
            AgentDeployment.org_id == organization.id
        )
        api_runs = await locker.rows(
            LockClass.ENVELOPE,
            select(AgentApiRun)
            .where(
                or_(
                    AgentApiRun.conversation_run_id.in_(run_ids),
                    AgentApiRun.conversation_run_id.is_(None)
                    & AgentApiRun.deployment_id.in_(org_deployments)
                    & AgentApiRun.status.not_in(_API_TERMINAL),
                )
            )
            .order_by(AgentApiRun.id),
        )
        trigger_runs = await locker.rows(
            LockClass.ENVELOPE,
            select(AgentTriggerRun)
            .where(
                or_(
                    AgentTriggerRun.conversation_run_id.in_(run_ids),
                    AgentTriggerRun.conversation_run_id.is_(None)
                    & AgentTriggerRun.agent_id.in_(agent_ids)
                    & AgentTriggerRun.status.not_in(_TRIGGER_TERMINAL),
                )
            )
            .order_by(AgentTriggerRun.id),
        )
    return ArchiveScope(
        agent_ids=agent_ids,
        triggers=tuple(triggers),
        deployments=tuple(deployments),
        conversation_ids=conversation_ids,
        runs=tuple(runs),
        inputs=tuple(inputs),
        api_runs=tuple(row for row in api_runs if row.conversation_run_id is not None),
        trigger_runs=tuple(row for row in trigger_runs if row.conversation_run_id is not None),
        unlinked_api_runs=tuple(row for row in api_runs if row.conversation_run_id is None),
        unlinked_trigger_runs=tuple(row for row in trigger_runs if row.conversation_run_id is None),
        lock_trace=tuple(locker.trace),
    )
