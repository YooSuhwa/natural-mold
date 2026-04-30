"""Assistant 도구 공통 헬퍼 — Agent eager-load 쿼리 중복 제거."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
from app.models.mcp_tool import AgentMcpToolLink
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink


async def get_agent_with_eager_load(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Agent | None:
    """Agent를 연관 관계(model, tool/mcp/skill links, sub_agent_links)와 함께 조회한다.

    read_tools, write_tools 양쪽에서 공통으로 사용한다.
    """
    # AgentSubAgentLink.sub_agent는 lazy="joined"라 link 로드 시 자동으로 함께 옴 —
    # 여기서 추가 selectinload는 중복.
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id, Agent.user_id == user_id)
        .options(
            selectinload(Agent.model),
            selectinload(Agent.tool_links).selectinload(AgentToolLink.tool),
            selectinload(Agent.mcp_tool_links).selectinload(AgentMcpToolLink.mcp_tool),
            selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
            selectinload(Agent.sub_agent_links),
        )
    )
    return result.scalar_one_or_none()
