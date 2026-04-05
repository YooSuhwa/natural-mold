from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.agent import Agent
from app.models.conversation import Conversation, Message
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
    await db.delete(conv)
    await db.commit()


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
            selectinload(Agent.tool_links)
            .selectinload(AgentToolLink.tool)
            .selectinload(Tool.mcp_server),
            selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
        )
    )
    return result.scalar_one_or_none()


def get_agent_skill_contents(agent: Agent) -> list[str]:
    """Get skill contents from an agent's eagerly-loaded skill links."""
    if not agent.skill_links:
        return []
    contents: list[str] = []
    for link in agent.skill_links:
        skill = link.skill
        if not skill:
            continue
        if skill.type == "package" and skill.storage_path:
            base_url = f"/api/skills/{skill.id}/files"
            header = f"Base directory for this skill: {base_url}\n\n"
            body = skill.content or ""
            body = body.replace("${SKILL_DIR}", base_url)
            body = body.replace("${CLAUDE_SKILL_DIR}", base_url)
            contents.append(header + body)
        else:
            contents.append(skill.content)
    return contents


def build_effective_prompt(agent: Agent) -> str:
    """Build system prompt with skill contents injected."""
    skill_contents = get_agent_skill_contents(agent)
    if not skill_contents:
        return agent.system_prompt
    skills_text = "\n\n---\n\n".join(skill_contents)
    return f"{agent.system_prompt}\n\n## 연결된 스킬\n\n{skills_text}"


def build_tools_config(agent: Agent, conversation_id: str | None = None) -> list[dict[str, Any]]:
    """Build tools_config list from agent's tool and skill links."""
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
            config_entry["name"] = tool.name
            config_entry["mcp_server_url"] = tool.mcp_server.url
            config_entry["mcp_tool_name"] = tool.name
        tools_config.append(config_entry)

    # Disambiguate duplicate MCP tool names by adding server prefix
    name_counts: dict[str, int] = {}
    for tc in tools_config:
        if tc.get("type") == "mcp":
            name_counts[tc["name"]] = name_counts.get(tc["name"], 0) + 1

    if any(c > 1 for c in name_counts.values()):
        for tc in tools_config:
            if tc.get("type") == "mcp" and name_counts.get(tc["name"], 0) > 1:
                # Only prefix duplicates — use server URL host as disambiguator
                from urllib.parse import urlparse

                host = urlparse(tc["mcp_server_url"]).netloc.replace(".", "_").replace(":", "_")
                tc["name"] = f"{host}_{tc['name']}"

    for link in agent.skill_links:
        skill = link.skill
        if skill and skill.type == "package" and skill.storage_path:
            output_dir = None
            if conversation_id:
                output_dir = str(Path(settings.conversation_output_dir) / conversation_id)
            tools_config.append(
                {
                    "type": "skill_package",
                    "skill_id": str(skill.id),
                    "skill_dir": skill.storage_path,
                    "conversation_id": conversation_id,
                    "output_dir": output_dir,
                }
            )

    return tools_config
