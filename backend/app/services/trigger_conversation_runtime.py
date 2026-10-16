from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import ResolvedRuntimePolicy
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scoped
from app.error_codes import conversation_not_found, trigger_not_found
from app.models.agent_trigger import AgentTrigger
from app.models.conversation import Conversation
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services.conversation_runtime_policy import (
    ensure_conversation_runtime_policy as _ensure_conversation_runtime_policy,
)


async def resolve_schedule_conversation(
    db: AsyncSession, trigger: AgentTrigger, context: OrgContext
) -> Conversation:
    from app.services.chat.conversations import create_conversation

    if (trigger.user_id, trigger.org_id, trigger.tenant_id) != (
        context.user_id,
        context.org_id,
        context.tenant_id,
    ):
        raise trigger_not_found()
    if trigger.conversation_policy == "selected_conversation":
        conversation = await db.scalar(
            private_scoped(
                select(Conversation).where(Conversation.id == trigger.target_conversation_id),
                Conversation,
                context,
            )
        )
        if conversation is None or conversation.agent_id != trigger.agent_id:
            raise conversation_not_found()
        return conversation
    if (
        trigger.conversation_policy != "new_per_run"
        and trigger.schedule_conversation_id is not None
    ):
        conversation = await db.scalar(
            private_scoped(
                select(Conversation).where(Conversation.id == trigger.schedule_conversation_id),
                Conversation,
                context,
            )
        )
        if conversation is None or conversation.agent_id != trigger.agent_id:
            raise conversation_not_found()
        return conversation
    conversation = await create_conversation(
        db, trigger.agent_id, f"스케줄: {trigger.name}", context=context
    )
    conversation.last_activity_source = "schedule"
    if trigger.conversation_policy != "new_per_run":
        trigger.schedule_conversation_id = conversation.id
    await db.flush()
    return conversation


async def ensure_schedule_conversation_runtime_policy(
    db: AsyncSession, target: ConversationPolicyTarget
) -> ResolvedRuntimePolicy:
    """Snapshot policy for the explicitly scoped trigger conversation."""
    _, _, runtime_policy = await _ensure_conversation_runtime_policy(db, target)
    return runtime_policy
