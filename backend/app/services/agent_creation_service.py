from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_creation_session import AgentCreationSession
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.agent_runtime.creation_agent import run_creation_conversation


async def create_session(db: AsyncSession, user_id: uuid.UUID) -> AgentCreationSession:
    session = AgentCreationSession(
        user_id=user_id,
        conversation_history=[],
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_session(
    db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> AgentCreationSession | None:
    result = await db.execute(
        select(AgentCreationSession).where(
            AgentCreationSession.id == session_id,
            AgentCreationSession.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def send_message(
    db: AsyncSession, session: AgentCreationSession, content: str
) -> dict:
    # Get available tools and models for context (include system tools)
    tools_result = await db.execute(
        select(Tool.name).where(
            or_(Tool.user_id == session.user_id, Tool.is_system.is_(True))
        )
    )
    available_tools = [r[0] for r in tools_result.all()]

    models_result = await db.execute(select(Model.display_name))
    available_models = [r[0] for r in models_result.all()]

    # Run creation agent
    response = await run_creation_conversation(
        conversation_history=session.conversation_history,
        user_message=content,
        available_tools=available_tools,
        available_models=available_models,
    )

    # Update session
    history = list(session.conversation_history)
    history.append({"role": "user", "content": content})
    history.append({"role": "assistant", "content": response["raw_content"]})
    session.conversation_history = history

    if response.get("draft_config"):
        session.draft_config = response["draft_config"]

    await db.commit()
    await db.refresh(session)

    return response


async def confirm_creation(
    db: AsyncSession, session: AgentCreationSession
) -> Agent | None:
    if not session.draft_config:
        return None

    config = session.draft_config

    # Find recommended model
    model_result = await db.execute(
        select(Model).where(Model.display_name == config.get("recommended_model", "GPT-4o"))
    )
    model = model_result.scalar_one_or_none()
    if not model:
        model_result = await db.execute(select(Model).where(Model.is_default == True))
        model = model_result.scalar_one_or_none()

    if not model:
        return None

    # Auto-link recommended tools by name (before db.add to avoid lazy loading)
    tools_to_link: list[Tool] = []
    recommended_names = config.get("recommended_tool_names", [])
    if recommended_names:
        lower_names = [n.lower() for n in recommended_names]
        tools_result = await db.execute(
            select(Tool).where(
                or_(Tool.user_id == session.user_id, Tool.is_system.is_(True)),
                func.lower(Tool.name).in_(lower_names),
            )
        )
        tools_to_link = list(tools_result.scalars().all())

    agent = Agent(
        user_id=session.user_id,
        name=config.get("name", "새 에이전트"),
        description=config.get("description"),
        system_prompt=config.get("system_prompt", ""),
        model_id=model.id,
    )
    agent.tool_links = [AgentToolLink(tool_id=t.id) for t in tools_to_link]
    db.add(agent)

    session.status = "completed"

    await db.commit()
    await db.refresh(agent, ["model", "tool_links"])
    return agent
