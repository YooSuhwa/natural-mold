"""Protect agent cascade deletion from durable chat run persistence."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scoped
from app.error_codes import agent_not_found
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun


async def delete_agent(db: AsyncSession, agent: Agent, context: OrgContext) -> None:
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    scoped = await db.scalar(
        private_scoped(select(Agent).where(Agent.id == agent.id), Agent, context)
    )
    if scoped is None:
        raise agent_not_found()
    agent = scoped
    # Match conversation deletion/run creation's fence before touching run rows.
    # A worker can hold a run lock while persisting events with conversation FKs;
    # cascading first reverses that lock order and can deadlock both transactions.
    conversation_ids = list(
        await db.scalars(
            select(Conversation.id)
            .where(Conversation.agent_id == agent.id)
            .order_by(Conversation.id)
            .with_for_update(of=Conversation)
        )
    )
    active_run_id = await db.scalar(
        select(ConversationRun.id)
        .where(
            ConversationRun.conversation_id.in_(conversation_ids),
            ConversationRun.is_active.is_(True),
        )
        .limit(1)
    )
    if active_run_id is not None:
        raise HTTPException(status_code=409, detail="Agent has an active conversation run")
    await db.delete(agent)
    await db.flush()
