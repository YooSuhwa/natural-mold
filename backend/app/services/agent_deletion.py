"""Archive mutations shared by lifecycle APIs and the legacy service facade."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
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
    await db.execute(
        delete(AgentSubAgentLink).where(
            or_(
                AgentSubAgentLink.parent_agent_id == request.agent_id,
                AgentSubAgentLink.sub_agent_id == request.agent_id,
            )
        )
    )
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
    """Compatibility facade: the former physical delete now archives in place."""
    await lock_agent_tree(db, agent.id)
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
    "stage_agent_archive",
]
