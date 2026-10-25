"""Archive mutations shared by lifecycle APIs and the legacy service facade."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_delegations import lock_delegations, sync_agent_delegations
from app.authz.org_context import OrgContext
from app.authz.organization_execution_prefix import mark_org_writer
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scoped
from app.error_codes import agent_not_found
from app.models.agent import Agent
from app.models.agent_api import AgentDeployment
from app.models.agent_subagent import AgentSubAgentLink
from app.models.agent_trigger import AgentTrigger
from app.models.conversation import Conversation
from app.models.conversation_run_input import ConversationRunInput
from app.models.share_link import ShareLink
from app.services.agent_execution_owner import (
    ParticipantCancelRequest,
    PreparedCancelIntent,
    stage_participant_cancel_intents,
)
from app.services.agent_lifecycle_locks import lock_agent_tree


@dataclass(frozen=True, slots=True)
class AgentArchiveMutation:
    cancel_intents: tuple[PreparedCancelIntent, ...]
    disabled_trigger_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class AgentArchiveRequest:
    agent_id: UUID
    actor_user_id: UUID
    archived_at: datetime


async def _revoke_subagent_delegations(
    db: AsyncSession, request: AgentArchiveRequest, parents: set[UUID]
) -> None:
    """F-02 (10.6): the removed subagent links' delegation grants go with them (the
    archived agent's own and its parents'); only subagent links are reconciled, so no
    private Agent column is read on this governance path. Tuples apply after commit."""
    scope = (
        await db.execute(select(Agent.org_id, Agent.tenant_id).where(Agent.id == request.agent_id))
    ).one()
    ctx = OrgContext(org_id=scope.org_id, tenant_id=scope.tenant_id, user_id=request.actor_user_id)
    for agent_id in sorted({request.agent_id, *parents}, key=lambda item: item.bytes):
        await sync_agent_delegations(db, ctx, agent_id, kinds=frozenset({"agent"}))


async def lock_archive_target(db: AsyncSession, agent_id: UUID, actor_user_id: UUID) -> None:
    """Lock the Organization row, then the Agent tree, before an archive is staged.

    The archive revokes the removed subagent links' delegations, and that revoke takes
    the Organization row ``FOR UPDATE`` (E-02). Taken after the lifecycle lock it
    inverts the canonical Organization -> Agent lifecycle order, so an execution
    holding the Organization ``FOR SHARE`` while it waits for this Agent's lifecycle
    lock (queue recovery, a run start) deadlocks with the archive.
    """
    scope = (
        await db.execute(select(Agent.org_id, Agent.tenant_id).where(Agent.id == agent_id))
    ).one_or_none()

    async def lock_organization() -> None:
        if scope is not None:
            ctx = OrgContext(org_id=scope.org_id, tenant_id=scope.tenant_id, user_id=actor_user_id)
            await lock_delegations(db, ctx)

    await lock_agent_tree(db, agent_id, parent_lock=lock_organization)


async def stage_agent_archive(
    db: AsyncSession,
    request: AgentArchiveRequest,
) -> AgentArchiveMutation:
    """Stage the complete durable archive mutation without committing."""
    locked_agent = (
        await db.execute(
            select(Agent.id, Agent.archived_at)
            .where(Agent.id == request.agent_id)
            .with_for_update(of=Agent)
        )
    ).one_or_none()
    if locked_agent is None:
        raise agent_not_found()
    archived_at = locked_agent.archived_at or request.archived_at
    if archived_at.tzinfo is not None:
        # An agent archived before reads back timezone-aware from PostgreSQL; the naive
        # UTC columns below (queue_paused_at, run inputs, share links, triggers) made asyncpg
        # refuse it, so re-deleting an archived agent with conversations was a 500.
        archived_at = archived_at.astimezone(UTC).replace(tzinfo=None)
    if locked_agent.archived_at is None:
        await db.execute(
            update(Agent)
            .where(Agent.id == request.agent_id, Agent.archived_at.is_(None))
            .values(archived_at=archived_at, archived_by=request.actor_user_id)
        )

    conversation_ids = tuple(
        await db.scalars(
            select(Conversation.id)
            .where(Conversation.agent_id == request.agent_id)
            .order_by(Conversation.id)
            .with_for_update(of=Conversation)
        )
    )
    if conversation_ids:
        await db.execute(
            update(Conversation)
            .where(Conversation.id.in_(conversation_ids))
            .values(
                agent_lifecycle_state="archived",
                agent_archived_at_snapshot=archived_at,
                queue_paused=True,
                queue_paused_at=archived_at,
            )
        )
        await db.execute(
            update(ConversationRunInput)
            .where(
                ConversationRunInput.conversation_id.in_(conversation_ids),
                ConversationRunInput.status == "pending",
            )
            .values(
                status="canceled",
                run_id=None,
                revision=ConversationRunInput.revision + 1,
                updated_at=archived_at,
            )
        )
        await db.execute(
            update(ShareLink)
            .where(
                ShareLink.conversation_id.in_(conversation_ids),
                ShareLink.revoked_at.is_(None),
            )
            .values(revoked_at=archived_at)
        )

    trigger_ids = tuple(
        await db.scalars(
            select(AgentTrigger.id)
            .where(
                AgentTrigger.agent_id == request.agent_id,
                AgentTrigger.status == "active",
            )
            .order_by(AgentTrigger.id)
            .with_for_update(of=AgentTrigger)
        )
    )
    if trigger_ids:
        await db.execute(
            update(AgentTrigger)
            .where(AgentTrigger.id.in_(trigger_ids))
            .values(status="paused", next_run_at=None)
        )
    await db.execute(
        update(AgentDeployment)
        .where(
            AgentDeployment.agent_id == request.agent_id,
            AgentDeployment.status == "active",
        )
        .values(status="disabled", updated_at=archived_at)
    )
    parents = set(
        await db.scalars(
            select(AgentSubAgentLink.parent_agent_id).where(
                AgentSubAgentLink.sub_agent_id == request.agent_id
            )
        )
    )
    await db.execute(
        delete(AgentSubAgentLink).where(
            or_(
                AgentSubAgentLink.parent_agent_id == request.agent_id,
                AgentSubAgentLink.sub_agent_id == request.agent_id,
            )
        )
    )
    await _revoke_subagent_delegations(db, request, parents)
    intents = await stage_participant_cancel_intents(
        db,
        ParticipantCancelRequest(
            agent_id_snapshot=request.agent_id,
            reason="agent_archived",
        ),
    )
    await db.flush()
    return AgentArchiveMutation(intents, trigger_ids)


async def delete_agent(db: AsyncSession, agent: Agent, context: OrgContext) -> None:
    """Compatibility facade: the former physical delete now archives in place.

    F-02: no app route calls it; it does not commit, so its queued delegation revokes
    stay on the session for the caller's ``commit_and_apply`` (or the outbox worker)."""
    mark_org_writer(db)  # archive may revoke: any prefix takes the org row FOR UPDATE
    await lock_archive_target(db, agent.id, context.user_id)
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    scoped_id = await db.scalar(
        private_scoped(select(Agent.id).where(Agent.id == agent.id), Agent, context)
    )
    if scoped_id is None:
        raise agent_not_found()
    await stage_agent_archive(
        db,
        AgentArchiveRequest(
            agent_id=scoped_id,
            actor_user_id=context.user_id,
            archived_at=datetime.now(UTC).replace(tzinfo=None),
        ),
    )


__all__ = [
    "AgentArchiveMutation",
    "AgentArchiveRequest",
    "delete_agent",
    "lock_archive_target",
    "stage_agent_archive",
]
