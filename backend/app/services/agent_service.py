from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
from app.models.template import Template
from app.models.tool import AgentToolLink, Tool
from app.schemas.agent import AgentCreate, AgentUpdate


def _selectin_agent() -> list:
    """Standard eager-loading options for Agent queries."""
    return [selectinload(Agent.model), selectinload(Agent.tool_links).selectinload(AgentToolLink.tool)]


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


def _build_tool_links(
    tool_ids: list[uuid.UUID],
    config_map: dict[uuid.UUID, dict[str, Any]],
) -> list[AgentToolLink]:
    """Create AgentToolLink objects with optional per-tool config."""
    return [
        AgentToolLink(tool_id=tid, config=config_map.get(tid))
        for tid in tool_ids
    ]


async def create_agent(db: AsyncSession, data: AgentCreate, user_id: uuid.UUID) -> Agent:
    agent = Agent(
        user_id=user_id,
        name=data.name,
        description=data.description,
        system_prompt=data.system_prompt,
        model_id=data.model_id,
        template_id=data.template_id,
    )

    # Build config map from tool_configs
    config_map: dict[uuid.UUID, dict[str, Any]] = {
        tc.tool_id: tc.config for tc in data.tool_configs if tc.config
    }

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
        agent.tool_links = _build_tool_links(tool_ids_to_link, config_map)

    db.add(agent)
    await db.commit()
    await db.refresh(agent, ["model", "tool_links"])
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
        config_map: dict[uuid.UUID, dict[str, Any]] = {}
        if data.tool_configs:
            config_map = {tc.tool_id: tc.config for tc in data.tool_configs if tc.config}
        agent.tool_links = _build_tool_links(data.tool_ids, config_map)
    elif data.tool_configs is not None:
        # Update configs only (no tool_ids change)
        config_map = {tc.tool_id: tc.config for tc in data.tool_configs}
        for link in agent.tool_links:
            if link.tool_id in config_map:
                link.config = config_map[link.tool_id]
    await db.commit()
    await db.refresh(agent, ["model", "tool_links"])
    return agent


async def delete_agent(db: AsyncSession, agent: Agent) -> None:
    await db.delete(agent)
    await db.commit()
