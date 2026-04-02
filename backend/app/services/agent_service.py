from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
from app.models.template import Template
from app.models.tool import Tool
from app.schemas.agent import AgentCreate, AgentUpdate


async def list_agents(db: AsyncSession, user_id: uuid.UUID) -> list[Agent]:
    result = await db.execute(
        select(Agent)
        .where(Agent.user_id == user_id)
        .options(selectinload(Agent.model), selectinload(Agent.tools))
        .order_by(Agent.created_at.desc())
    )
    return list(result.scalars().all())


async def get_agent(db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID) -> Agent | None:
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id, Agent.user_id == user_id)
        .options(selectinload(Agent.model), selectinload(Agent.tools))
    )
    return result.scalar_one_or_none()


async def create_agent(db: AsyncSession, data: AgentCreate, user_id: uuid.UUID) -> Agent:
    agent = Agent(
        user_id=user_id,
        name=data.name,
        description=data.description,
        system_prompt=data.system_prompt,
        model_id=data.model_id,
        template_id=data.template_id,
    )

    # Collect tools to link
    tool_ids_to_link: list[uuid.UUID] = []

    if data.tool_ids:
        tool_ids_to_link.extend(data.tool_ids)
    elif data.template_id:
        # Auto-link from template recommended tools (by name)
        template = await db.get(Template, data.template_id)
        if template and template.recommended_tools:
            lower_names = [n.lower() for n in template.recommended_tools]
            result = await db.execute(
                select(Tool.id).where(
                    or_(Tool.user_id == user_id, Tool.is_system.is_(True)),
                    func.lower(Tool.name).in_(lower_names),
                )
            )
            tool_ids_to_link.extend(r[0] for r in result.all())

    if tool_ids_to_link:
        result = await db.execute(select(Tool).where(Tool.id.in_(tool_ids_to_link)))
        agent.tools = list(result.scalars().all())

    db.add(agent)
    await db.commit()
    await db.refresh(agent, ["model", "tools"])
    return agent


async def update_agent(
    db: AsyncSession, agent: Agent, data: AgentUpdate
) -> Agent:
    if data.name is not None:
        agent.name = data.name
    if data.description is not None:
        agent.description = data.description
    if data.system_prompt is not None:
        agent.system_prompt = data.system_prompt
    if data.model_id is not None:
        agent.model_id = data.model_id
    if data.tool_ids is not None:
        result = await db.execute(select(Tool).where(Tool.id.in_(data.tool_ids)))
        agent.tools = list(result.scalars().all())
    await db.commit()
    await db.refresh(agent, ["model", "tools"])
    return agent


async def delete_agent(db: AsyncSession, agent: Agent) -> None:
    await db.delete(agent)
    await db.commit()
