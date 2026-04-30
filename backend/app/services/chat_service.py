"""Chat service — conversations, messages, and agent context assembly.

Greenfield M5 rewrite. The legacy PREBUILT/CUSTOM/MCP branching has been
collapsed into a single resolution path: every tool row points at a registered
``ToolDefinition`` (``tool.definition_key``) plus an optional credential
(``tool.credential_id``). MCP server bindings are handled separately by the
caller via the new ``app.mcp.client`` module.

Helpers re-exported by this module are imported by the trigger executor and the
conversations router; their public shape (``get_agent_with_tools``,
``build_tools_config``, ``build_effective_prompt``, ``build_agent_skills``) is
preserved to keep those callers thin.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.credentials import service as credential_service
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import AgentSkillLink
from app.models.token_usage import TokenUsage
from app.models.tool import AgentToolLink, Tool
from app.schemas.conversation import ConversationUpdate
from app.skills.runtime import build_skills_for_agent

logger = logging.getLogger(__name__)


__all__ = [
    "build_agent_skills",
    "build_effective_prompt",
    "build_tools_config",
    "create_conversation",
    "delete_conversation",
    "get_agent_with_tools",
    "get_conversation",
    "list_conversations",
    "list_messages_from_checkpointer",
    "maybe_set_auto_title",
    "save_token_usage",
    "touch_conversation",
    "update_conversation",
]


# ---------------------------------------------------------------------------
# Conversations CRUD
# ---------------------------------------------------------------------------


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
    db: AsyncSession,
    conversation: Conversation,
) -> list:
    """Return persisted messages, attaching stable per-message timestamps.

    LangChain ``BaseMessage`` carries no timestamp metadata, so we keep an
    ``idx → ISO`` mapping in ``Conversation.message_timestamps``. The first
    time a message is exposed we stamp it with the current time; subsequent
    reads reuse the stored value so old messages don't drift on every fetch.
    """

    from app.agent_runtime.checkpointer import get_checkpointer
    from app.agent_runtime.message_utils import langchain_messages_to_response, parse_msg_id

    checkpointer = get_checkpointer()
    config = {"configurable": {"thread_id": str(conversation.id)}}
    checkpoint_tuple = await checkpointer.aget_tuple(config)  # type: ignore[arg-type]

    if not checkpoint_tuple:
        return []

    messages = checkpoint_tuple.checkpoint.get("channel_values", {}).get("messages", [])

    new_stored: dict[str, str] = dict(conversation.message_timestamps or {})
    timestamps: list[datetime] = []
    now = datetime.now(UTC).replace(tzinfo=None)
    changed = False

    for idx, msg in enumerate(messages):
        msg_uuid = parse_msg_id(getattr(msg, "id", None), conversation.id, idx)
        key = str(msg_uuid)
        iso = new_stored.get(key)
        if iso:
            ts = datetime.fromisoformat(iso)
        else:
            ts = now + timedelta(milliseconds=idx)
            new_stored[key] = ts.isoformat()
            changed = True
        timestamps.append(ts)

    if changed:
        await db.execute(
            update(Conversation)
            .where(Conversation.id == conversation.id)
            .values(message_timestamps=new_stored)
        )
        await db.commit()

    return langchain_messages_to_response(messages, conversation.id, timestamps=timestamps)


async def maybe_set_auto_title(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    content: str,
) -> None:
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


async def touch_conversation(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Bump ``conversation.updated_at`` to anchor message-list timestamps."""

    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=datetime.now(UTC).replace(tzinfo=None))
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Agent context assembly (single-path, greenfield)
# ---------------------------------------------------------------------------


async def get_agent_with_tools(
    db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID
) -> Agent | None:
    """Load agent with everything needed by the runtime in one round-trip.

    Eager-loads:
    - ``Agent.model`` (no provider join — ``llm_providers`` is retired)
    - ``Agent.tool_links → tool → credential`` (single FK path)
    - ``Agent.skill_links → skill``

    The legacy per-user "default connection map" prefetch is gone: every tool
    row owns its own ``credential_id``. The trigger executor and conversations
    router both call this helper, so prefetch is consistent across the two
    callers (closing the M11 ``trigger_executor.py`` prefetch-skew bug).
    """

    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id, Agent.user_id == user_id)
        .options(
            selectinload(Agent.model),
            selectinload(Agent.llm_credential),
            selectinload(Agent.tool_links)
            .selectinload(AgentToolLink.tool)
            .selectinload(Tool.credential),
            # MCP tool link → mcp_tool → server (server carries transport,
            # url, headers, and credential needed to actually invoke).
            selectinload(Agent.mcp_tool_links)
            .selectinload(AgentMcpToolLink.mcp_tool)
            .selectinload(McpTool.server)
            .selectinload(McpServer.credential),
            selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
        )
    )
    return result.scalar_one_or_none()


def build_effective_prompt(agent: Agent) -> str:
    """Build system prompt — skill bodies are injected by deepagents middleware."""

    return agent.system_prompt


def build_agent_skills(agent: Agent) -> list[dict[str, Any]]:
    """Forward the agent's skill links to the runtime descriptor list."""

    return build_skills_for_agent(agent.skill_links)


async def build_tools_config(
    agent: Agent,
    *,
    db: AsyncSession | None = None,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build the runtime tools_config list for an agent.

    The shape is intentionally minimal — every entry exposes the registry
    ``definition_key`` (so the executor knows which runner to instantiate),
    the user-supplied ``parameters``, and an optional decrypted
    ``credentials`` dict.

    The legacy 4-way ``ToolType`` branch + per-user default connection map +
    cross-tenant ownership gates are gone. ``Tool.credential_id`` is the only
    auth source; ownership is enforced by ``user_id`` filters on the writes.

    MCP tools are not represented as ``Tool`` rows under the greenfield model
    (they live in ``mcp_servers``/``mcp_tools``). Until ``agent_mcp_servers``
    link binding ships, this function only emits regular tool entries; MCP
    bindings will come in via a separate config list. See
    ``app/mcp/client.py`` and the M5 follow-up note in ``progress.txt``.
    """

    configs: list[dict[str, Any]] = []
    for link in agent.tool_links:
        tool = link.tool
        if tool is None or not tool.enabled:
            continue

        credentials: dict[str, Any] | None = None
        credential = getattr(tool, "credential", None)
        if credential is not None:
            try:
                credentials = await credential_service.decrypt_with_external(
                    credential.data_encrypted
                )
            except Exception:  # noqa: BLE001 — surface as missing creds, never crash chat
                logger.exception(
                    "credential decryption failed for tool %s (credential %s)",
                    tool.id,
                    credential.id,
                )
                credentials = None

        configs.append(
            {
                "tool_id": str(tool.id),
                "definition_key": tool.definition_key,
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters or {}),
                "credentials": credentials,
                "credential_id": (
                    str(tool.credential_id) if tool.credential_id else None
                ),
                # Hook-framework correlation — wire down to ``tool_factory``.
                "user_id": str(agent.user_id),
                "agent_id": str(agent.id),
            }
        )

    # MCP tool bindings — emit in the executor's mcp_server_url shape so
    # ``_build_mcp_tools`` instantiates them. m25 added the link table that
    # makes this possible (previously a m5 follow-up).
    for mcp_link in agent.mcp_tool_links:
        mcp_tool = mcp_link.mcp_tool
        if mcp_tool is None or not mcp_tool.enabled:
            continue
        server = mcp_tool.server
        if server is None or not server.url:
            continue

        mcp_credentials: dict[str, Any] | None = None
        if server.credential is not None:
            try:
                mcp_credentials = await credential_service.decrypt_with_external(
                    server.credential.data_encrypted
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "credential decryption failed for mcp server %s", server.id
                )

        configs.append(
            {
                "tool_id": f"mcp:{mcp_tool.id}",
                "definition_key": "mcp",
                "name": mcp_tool.name,
                "description": mcp_tool.description,
                "parameters": {},
                # _build_mcp_tools branches on these keys (see executor.py
                # ``mcp_server_url``).
                "mcp_server_url": server.url,
                "mcp_tool_name": mcp_tool.name,
                "mcp_transport_headers": dict(server.headers or {}),
                "credentials": mcp_credentials,
                "user_id": str(agent.user_id),
                "agent_id": str(agent.id),
            }
        )

    return configs
