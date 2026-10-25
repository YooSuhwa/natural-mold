"""Serialized Agent archive, restore, and governance-list service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_lifecycle_scope import (
    AgentLifecycleAuthorization,
    authorize_agent_lifecycle,
    load_agent_lifecycle_authority,
    record_agent_lifecycle_success,
)
from app.authz.error_codes import agent_lifecycle_execution_pending
from app.dependencies import CurrentUser
from app.error_codes import agent_not_found
from app.exceptions import AppError
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.agent_purge import AgentPurgeReceipt
from app.models.conversation import Conversation
from app.schemas.agent_lifecycle import AgentLifecycleMetadataResponse, AgentRestoreResponse
from app.services.agent_deletion import (
    AgentArchiveRequest,
    lock_archive_target,
    stage_agent_archive,
)
from app.services.agent_execution_owner import PreparedCancelIntent
from app.services.agent_lifecycle_projection import (
    lifecycle_metadata,
    lifecycle_state,
    lock_lifecycle_target,
)


@dataclass(frozen=True, slots=True)
class AgentArchiveResult:
    metadata: AgentLifecycleMetadataResponse
    cancel_intents: tuple[PreparedCancelIntent, ...]
    disabled_trigger_ids: tuple[UUID, ...]


async def archive_agent(
    db: AsyncSession,
    actor: CurrentUser,
    agent_id: UUID,
) -> AgentArchiveResult:
    """Stage one atomic archive transaction and return post-commit notifications."""
    await lock_archive_target(db, agent_id, actor.id)
    authority = await authorize_agent_lifecycle(
        db, actor, AgentLifecycleAuthorization(agent_id=agent_id, action="archive")
    )
    archived_at = authority.archived_at or datetime.now(UTC).replace(tzinfo=None)
    mutation = await stage_agent_archive(
        db,
        AgentArchiveRequest(
            agent_id=agent_id,
            actor_user_id=actor.id,
            archived_at=archived_at,
        ),
    )
    if authority.archived_at is None:
        await record_agent_lifecycle_success(db, authority, "archive")
    refreshed = await load_agent_lifecycle_authority(db, actor, agent_id)
    state = await lifecycle_state(db, refreshed)
    metadata = lifecycle_metadata(refreshed, state)
    return AgentArchiveResult(
        metadata=metadata,
        cancel_intents=mutation.cancel_intents,
        disabled_trigger_ids=mutation.disabled_trigger_ids,
    )


async def restore_agent(
    db: AsyncSession,
    actor: CurrentUser,
    agent_id: UUID,
) -> AgentRestoreResponse:
    """Restore only an archived Agent whose durable work has fully acknowledged stop."""
    await lock_lifecycle_target(db, agent_id)
    authority = await authorize_agent_lifecycle(
        db, actor, AgentLifecycleAuthorization(agent_id=agent_id, action="restore")
    )
    preparing_receipt = await db.scalar(
        select(AgentPurgeReceipt.id)
        .join(
            Agent,
            (Agent.id == AgentPurgeReceipt.agent_id_snapshot)
            & (Agent.tenant_id == AgentPurgeReceipt.tenant_id)
            & (Agent.org_id == AgentPurgeReceipt.org_id),
        )
        .where(
            Agent.id == agent_id,
            AgentPurgeReceipt.state == "preparing",
        )
        .with_for_update(of=AgentPurgeReceipt)
    )
    if preparing_receipt is not None:
        raise agent_lifecycle_execution_pending()
    if authority.archived_at is None:
        return AgentRestoreResponse.model_validate(lifecycle_metadata(authority, "active"))
    if await lifecycle_state(db, authority) != "archived_stopped":
        raise agent_lifecycle_execution_pending()
    locked_agent_id = await db.scalar(
        select(Agent.id).where(Agent.id == agent_id).with_for_update(of=Agent)
    )
    if locked_agent_id is None:
        raise agent_not_found()
    await db.execute(
        update(Agent).where(Agent.id == locked_agent_id).values(archived_at=None, archived_by=None)
    )
    await db.execute(
        update(Conversation)
        .where(Conversation.agent_id == agent_id)
        .values(
            agent_lifecycle_state="active",
            agent_archived_at_snapshot=None,
            queue_paused=False,
            queue_paused_at=None,
        )
    )
    await record_agent_lifecycle_success(db, authority, "restore")
    await db.flush()
    refreshed = await load_agent_lifecycle_authority(db, actor, agent_id)
    return AgentRestoreResponse.model_validate(lifecycle_metadata(refreshed, "active"))


async def list_archived_agents(
    db: AsyncSession,
    actor: CurrentUser,
) -> list[AgentLifecycleMetadataResponse]:
    """Return metadata only for archived Agents the actor may govern."""
    agent_ids = tuple(
        await db.scalars(
            select(Agent.id)
            .where(
                Agent.archived_at.is_not(None),
                Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
            )
            .order_by(Agent.archived_at.desc(), Agent.id)
        )
    )
    result: list[AgentLifecycleMetadataResponse] = []
    for archived_id in agent_ids:
        try:
            authority = await load_agent_lifecycle_authority(db, actor, archived_id)
        except AppError as error:
            if error.status in {403, 404}:
                continue
            raise
        state = await lifecycle_state(db, authority)
        metadata = lifecycle_metadata(authority, state)
        result.append(metadata)
    return result


__all__ = ["AgentArchiveResult", "archive_agent", "list_archived_agents", "restore_agent"]
