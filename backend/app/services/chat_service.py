from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.skill import AgentSkillLink
from app.models.token_usage import TokenUsage
from app.models.tool import AgentToolLink, Tool
from app.schemas.conversation import ConversationUpdate


async def list_conversations(db: AsyncSession, agent_id: uuid.UUID) -> list[Conversation]:
    result = await db.execute(
        select(Conversation)
        .where(Conversation.agent_id == agent_id)
        .order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc())
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


async def get_conversation(db: AsyncSession, conversation_id: uuid.UUID) -> Conversation | None:
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    return result.scalar_one_or_none()


async def update_conversation(
    db: AsyncSession, conv: Conversation, data: ConversationUpdate
) -> Conversation:
    if data.title is not None:
        conv.title = data.title
    if data.is_pinned is not None:
        conv.is_pinned = data.is_pinned
    await db.commit()
    await db.refresh(conv)
    return conv


async def delete_conversation(db: AsyncSession, conv: Conversation) -> None:
    from app.agent_runtime.checkpointer import delete_thread

    await delete_thread(str(conv.id))
    await db.delete(conv)
    await db.commit()


async def list_messages_from_checkpointer(
    conversation_id: uuid.UUID,
    base_timestamp: datetime | None = None,
) -> list:
    """Checkpointer에서 대화 메시지를 조회하여 MessageResponse 리스트로 반환."""
    from app.agent_runtime.checkpointer import get_checkpointer
    from app.agent_runtime.message_utils import langchain_messages_to_response

    checkpointer = get_checkpointer()
    config = {"configurable": {"thread_id": str(conversation_id)}}
    checkpoint_tuple = await checkpointer.aget_tuple(config)

    if not checkpoint_tuple:
        return []

    messages = checkpoint_tuple.checkpoint.get("channel_values", {}).get("messages", [])
    return langchain_messages_to_response(messages, conversation_id, base_timestamp)


async def maybe_set_auto_title(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    content: str,
) -> None:
    """첫 사용자 메시지일 때 대화 제목을 자동 설정.

    Conversation.title이 기본값('새 대화')인 경우에만 UPDATE 실행.
    """
    title = content.strip().replace("\n", " ")
    if len(title) > 40:
        title = title[:37] + "..."
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id, Conversation.title == "새 대화")
        .values(title=title)
    )
    await db.commit()


async def save_token_usage(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    estimated_cost: float | None = None,
) -> TokenUsage:
    usage = TokenUsage(
        conversation_id=conversation_id,
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
            selectinload(Agent.tool_links)
            .selectinload(AgentToolLink.tool)
            .selectinload(Tool.mcp_server),
            selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
        )
    )
    return result.scalar_one_or_none()


def build_effective_prompt(agent: Agent) -> str:
    """Build system prompt (skill injection handled by deepagents SkillsMiddleware)."""
    return agent.system_prompt


def build_agent_skills(agent: Agent) -> list[dict[str, Any]]:
    """Build agent_skills list from agent's skill links (package skills with storage_path)."""
    return [
        {"skill_id": str(link.skill.id), "storage_path": link.skill.storage_path}
        for link in agent.skill_links
        if link.skill and link.skill.storage_path
    ]


def build_tools_config(agent: Agent, conversation_id: str | None = None) -> list[dict[str, Any]]:
    """Build tools_config list from agent's tool links."""
    tools_config: list[dict[str, Any]] = []

    for link in agent.tool_links:
        tool = link.tool
        merged_auth = {**(tool.auth_config or {}), **(link.config or {})}
        config_entry: dict[str, Any] = {
            "type": tool.type,
            "name": tool.name,
            "description": tool.description,
            "api_url": tool.api_url,
            "http_method": tool.http_method,
            "parameters_schema": tool.parameters_schema,
            "auth_type": tool.auth_type,
            "auth_config": merged_auth or None,
        }
        if tool.type == "mcp" and tool.mcp_server:
            config_entry["mcp_server_url"] = tool.mcp_server.url
            config_entry["mcp_tool_name"] = tool.name
        tools_config.append(config_entry)

    return tools_config
