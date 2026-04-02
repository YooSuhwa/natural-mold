from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
from app.models.conversation import Conversation, Message
from app.models.token_usage import TokenUsage
from app.models.tool import AgentToolLink


async def list_conversations(
    db: AsyncSession, agent_id: uuid.UUID
) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.agent_id == agent_id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


async def create_conversation(
    db: AsyncSession, agent_id: uuid.UUID, title: str | None = None
) -> Conversation:
    conv = Conversation(agent_id=agent_id, title=title or "새 대화")
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


async def get_conversation(
    db: AsyncSession, conversation_id: uuid.UUID
) -> Conversation | None:
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    return result.scalar_one_or_none()


async def list_messages(
    db: AsyncSession, conversation_id: uuid.UUID, limit: int = 100
) -> list[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def save_message(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_call_id: str | None = None,
) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # Auto-generate title from first user message (single UPDATE, no extra SELECT)
    if role == "user":
        title = content.strip().replace("\n", " ")
        if len(title) > 40:
            title = title[:37] + "..."
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.title == "새 대화")
            .values(title=title)
        )
        await db.commit()

    return msg


async def save_token_usage(
    db: AsyncSession,
    message_id: uuid.UUID,
    agent_id: uuid.UUID,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    estimated_cost: float | None = None,
) -> TokenUsage:
    usage = TokenUsage(
        message_id=message_id,
        agent_id=agent_id,
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        estimated_cost=estimated_cost,
    )
    db.add(usage)
    await db.commit()
    return usage


async def get_agent_with_tools(
    db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID
) -> Agent | None:
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id, Agent.user_id == user_id)
        .options(
            selectinload(Agent.model),
            selectinload(Agent.tool_links).selectinload(AgentToolLink.tool),
            selectinload(Agent.skill_links),
        )
    )
    return result.scalar_one_or_none()


async def get_agent_skill_contents(db: AsyncSession, agent: Agent) -> list[str]:
    """Get skill contents for an agent's linked skills."""
    from app.models.skill import Skill

    if not agent.skill_links:
        return []
    skill_ids = [link.skill_id for link in agent.skill_links]
    result = await db.execute(
        select(Skill.content).where(Skill.id.in_(skill_ids))
    )
    return [r[0] for r in result.all()]
