"""Detach retained historical rows from one live Agent identity."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.builder_session import BuilderSession
from app.models.conversation_artifact import ConversationArtifact
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.skill_usage_event import SkillUsageEvent
from app.models.token_usage import TokenUsage


async def detach_retained_agent_references(db: AsyncSession, agent_id: UUID) -> None:
    """Null only live Agent FKs whose rows belong to retained history."""
    for model in (
        ConversationRun,
        ConversationRunInput,
        ConversationRunAgent,
        ConversationArtifact,
        TokenUsage,
        McpAppInvocationBinding,
        AgentTriggerRun,
        BuilderSession,
        SkillUsageEvent,
    ):
        await db.execute(update(model).where(model.agent_id == agent_id).values(agent_id=None))


__all__ = ["detach_retained_agent_references"]
