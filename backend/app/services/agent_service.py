from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
from app.models.skill import AgentSkillLink
from app.models.template import Template
from app.models.tool import AgentToolLink, Tool
from app.schemas.agent import AgentCreate, AgentUpdate


def _selectin_agent() -> list:
    """Standard eager-loading options for Agent queries."""
    return [
        selectinload(Agent.model),
        selectinload(Agent.tool_links).selectinload(AgentToolLink.tool),
        selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
    ]


async def list_agents(db: AsyncSession, user_id: uuid.UUID) -> list[Agent]:
    result = await db.execute(
        select(Agent)
        .where(Agent.user_id == user_id)
        .options(*_selectin_agent())
        .order_by(Agent.created_at.desc())
    )
    return list(result.scalars().all())


async def get_agent(db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID) -> Agent | None:
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id, Agent.user_id == user_id)
        .options(*_selectin_agent())
    )
    return result.scalar_one_or_none()


def _build_tool_links(tool_ids: list[uuid.UUID]) -> list[AgentToolLink]:
    """Create AgentToolLink objects for the given tool ids."""
    return [AgentToolLink(tool_id=tid) for tid in tool_ids]


async def toggle_favorite(db: AsyncSession, agent: Agent) -> Agent:
    agent.is_favorite = not agent.is_favorite
    await db.commit()
    await db.refresh(agent, ["model", "tool_links", "skill_links"])
    return agent


async def create_agent(db: AsyncSession, data: AgentCreate, user_id: uuid.UUID) -> Agent:
    agent = Agent(
        user_id=user_id,
        name=data.name,
        description=data.description,
        system_prompt=data.system_prompt,
        model_id=data.model_id,
        model_params=data.model_params,
        middleware_configs=[mc.model_dump() for mc in data.middleware_configs]
        if data.middleware_configs
        else None,
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
        agent.tool_links = _build_tool_links(tool_ids_to_link)

    if data.skill_ids:
        agent.skill_links = [AgentSkillLink(skill_id=sid) for sid in data.skill_ids]

    db.add(agent)
    await db.commit()
    await db.refresh(agent, ["model", "tool_links", "skill_links"])
    return agent


async def update_agent(db: AsyncSession, agent: Agent, data: AgentUpdate) -> Agent:
    if data.name is not None:
        agent.name = data.name
    if data.description is not None:
        agent.description = data.description
    if data.system_prompt is not None:
        agent.system_prompt = data.system_prompt
    if data.model_id is not None:
        agent.model_id = data.model_id
    if data.is_favorite is not None:
        agent.is_favorite = data.is_favorite
    if data.model_params is not None:
        agent.model_params = data.model_params
    if data.middleware_configs is not None:
        agent.middleware_configs = [mc.model_dump() for mc in data.middleware_configs]
    if data.tool_ids is not None:
        # Clear existing links first to avoid PK conflict, then add new ones
        agent.tool_links.clear()
        await db.flush()
        agent.tool_links = _build_tool_links(data.tool_ids)
    if data.skill_ids is not None:
        agent.skill_links.clear()
        await db.flush()
        agent.skill_links = [AgentSkillLink(skill_id=sid) for sid in data.skill_ids]
    await db.commit()
    await db.refresh(agent, ["model", "tool_links", "skill_links"])
    return agent


async def delete_agent(db: AsyncSession, agent: Agent) -> None:
    await db.delete(agent)
    await db.commit()
