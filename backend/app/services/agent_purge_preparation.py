"""Transaction-A preparation for one idempotent Agent purge."""

from __future__ import annotations

from sqlalchemy import select

from app.authz.agent_lifecycle_scope import (
    AgentLifecycleAuthorization,
    authorize_agent_lifecycle,
)
from app.authz.error_codes import agent_lifecycle_execution_pending, agent_not_archived
from app.models.agent import Agent
from app.models.agent_purge import AgentPurgeReceipt
from app.models.conversation import Conversation
from app.services.agent_lifecycle_projection import lifecycle_state, lock_lifecycle_target
from app.services.agent_purge_authority import authorize_purge_replay
from app.services.agent_purge_types import AgentPurgeCommand, PreparedAgentPurge


def _prepared_replay(receipt: AgentPurgeReceipt) -> PreparedAgentPurge:
    return PreparedAgentPurge(
        receipt.id,
        receipt.tenant_id,
        receipt.org_id,
        receipt.owner_user_id_snapshot,
        receipt.agent_name_snapshot,
        (),
        database_deleted=receipt.state != "preparing",
    )


async def prepare_agent_purge(command: AgentPurgeCommand) -> PreparedAgentPurge:
    """Create/reuse the durable receipt after fresh authority and stop checks."""
    db = command.db
    agent_exists = await db.scalar(select(Agent.id).where(Agent.id == command.agent_id))
    if agent_exists is None:
        return _prepared_replay(await authorize_purge_replay(command))

    await lock_lifecycle_target(db, command.agent_id)
    agent_exists = await db.scalar(
        select(Agent.id)
        .where(Agent.id == command.agent_id)
        .execution_options(populate_existing=True)
    )
    if agent_exists is None:
        return _prepared_replay(await authorize_purge_replay(command))
    authority = await authorize_agent_lifecycle(
        db,
        command.actor,
        AgentLifecycleAuthorization(
            command.agent_id,
            "purge",
            command.confirmation_name,
        ),
    )
    if authority.archived_at is None:
        raise agent_not_archived()
    if await lifecycle_state(db, authority) != "archived_stopped":
        raise agent_lifecycle_execution_pending()
    agent_scope = (
        await db.execute(
            select(Agent.tenant_id, Agent.org_id)
            .where(Agent.id == command.agent_id)
            .with_for_update(of=Agent)
        )
    ).one()
    receipt = await db.scalar(
        select(AgentPurgeReceipt)
        .where(
            AgentPurgeReceipt.tenant_id == agent_scope.tenant_id,
            AgentPurgeReceipt.org_id == agent_scope.org_id,
            AgentPurgeReceipt.agent_id_snapshot == command.agent_id,
        )
        .with_for_update()
    )
    if receipt is None:
        receipt = AgentPurgeReceipt(
            tenant_id=agent_scope.tenant_id,
            org_id=agent_scope.org_id,
            agent_id_snapshot=command.agent_id,
            owner_user_id_snapshot=authority.owner_user_id,
            agent_name_snapshot=authority.name,
            requester_user_id=authority.actor_user_id,
            state="preparing",
        )
        db.add(receipt)
        await db.flush()
    conversation_ids = tuple(
        await db.scalars(
            select(Conversation.id)
            .where(Conversation.agent_id == command.agent_id)
            .order_by(Conversation.id)
        )
    )
    return PreparedAgentPurge(
        receipt.id,
        receipt.tenant_id,
        receipt.org_id,
        receipt.owner_user_id_snapshot,
        receipt.agent_name_snapshot,
        conversation_ids,
        database_deleted=receipt.state != "preparing",
    )


__all__ = ["prepare_agent_purge"]
