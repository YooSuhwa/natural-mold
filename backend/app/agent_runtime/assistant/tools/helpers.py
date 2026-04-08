"""Assistant 도구 공통 헬퍼 — Agent eager-load 쿼리 중복 제거."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink


async def get_agent_with_eager_load(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Agent | None:
    """Agent를 연관 관계(model, tool_links, skill_links)와 함께 조회한다.

    read_tools, write_tools 양쪽에서 공통으로 사용한다.
    """
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id, Agent.user_id == user_id)
        .options(
            selectinload(Agent.model),
            selectinload(Agent.tool_links).selectinload(AgentToolLink.tool),
            selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
        )
    )
    return result.scalar_one_or_none()
