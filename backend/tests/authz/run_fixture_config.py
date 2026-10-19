"""Real worker configurations for explicitly supplied disposable test sessions."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime.runtime_config import AgentConfig
from app.authz.org_context import OrgContext
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun


async def run_fixture_config(
    run_id: UUID, sessions: async_sessionmaker[AsyncSession], *, model_name: str = "fixture"
) -> AgentConfig:
    async with sessions() as db:
        run = await db.get(ConversationRun, run_id)
        assert run is not None
        conversation = await db.get(Conversation, run.conversation_id)
        assert conversation is not None
        return AgentConfig(
            provider="openai",
            model_name=model_name,
            api_key=None,
            base_url=None,
            system_prompt="Fixture",
            tools_config=[],
            thread_id=str(conversation.id),
            agent_id=str(run.agent_id),
            user_id=str(run.user_id),
            org_context=OrgContext(
                user_id=conversation.user_id,
                org_id=conversation.org_id,
                tenant_id=conversation.tenant_id,
            ),
        )
