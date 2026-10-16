"""Validate detached Trigger roots and references before claiming a durable run.

At fire time the trigger owner must still hold ``agent#can_run`` (plan 11.6, D-06),
decided by the process service (``runtime_authz()``; legacy: no-op). A denial is an
``AppError`` the claim logs and skips; recording ``permission_revoked`` runs is F-08.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.conversation_agent_checks import require_agent_run
from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scope_predicate
from app.error_codes import conversation_not_found, trigger_not_found
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.conversation import Conversation


async def require_trigger_actor(db: AsyncSession, trigger: AgentTrigger) -> OrgContext:
    """No private run/counter/conversation writes precede successful validation."""
    with db.no_autoflush:
        context = await resolve_private_actor(
            db, PrivateResourceActor(trigger.user_id, trigger.org_id, trigger.tenant_id)
        )
        agent_id = await db.scalar(
            select(Agent.id).where(
                Agent.id == trigger.agent_id, private_scope_predicate(Agent, context)
            )
        )
        if agent_id is None:
            raise trigger_not_found()
        if (
            trigger.conversation_policy == "selected_conversation"
            and not trigger.target_conversation_id
        ):
            raise conversation_not_found()
        for reference in (trigger.schedule_conversation_id, trigger.target_conversation_id):
            if reference is None:
                continue
            conversation_id = await db.scalar(
                select(Conversation.id).where(
                    Conversation.id == reference,
                    Conversation.agent_id == agent_id,
                    private_scope_predicate(Conversation, context),
                )
            )
            if conversation_id is None:
                raise conversation_not_found()
    await require_agent_run(context, agent_id, authz=runtime.runtime_authz())
    return context
