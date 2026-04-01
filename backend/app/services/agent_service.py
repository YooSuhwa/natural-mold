from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
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
    )
    if data.tool_ids:
        result = await db.execute(select(Tool).where(Tool.id.in_(data.tool_ids)))
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
