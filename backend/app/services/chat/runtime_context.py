"""Agent runtime context assembly (single-path, greenfield).

BE-S1 split from ``app.services.chat_service`` — pure move, no behavior
change. The public shape (``get_agent_with_tools``, ``build_tools_config``,
``build_effective_prompt``, ``build_agent_skills``) is re-exported by the
``chat_service`` facade for the trigger executor and conversations router.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.agent_runtime.identity import AgentRunIdentity
from app.credentials import service as credential_service
from app.exceptions import ValidationError
from app.mcp.auth import resolve_mcp_auth
from app.mcp.client import build_headers
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink, Tool
from app.skills.runtime import build_skills_for_agent
from app.tools.risk import trigger_blocked_tools

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent context assembly (single-path, greenfield)
# ---------------------------------------------------------------------------


def _agent_runtime_load_options() -> list[Any]:
    child_agent = AgentSubAgentLink.sub_agent
    return [
        selectinload(Agent.model),
        selectinload(Agent.llm_credential),
        selectinload(Agent.tool_links)
        .selectinload(AgentToolLink.tool)
        .selectinload(Tool.credential),
        selectinload(Agent.mcp_tool_links)
        .selectinload(AgentMcpToolLink.mcp_tool)
        .selectinload(McpTool.server)
        .selectinload(McpServer.credential),
        selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
        selectinload(Agent.sub_agent_links)
        .joinedload(child_agent)
        .options(
            selectinload(Agent.model),
            selectinload(Agent.llm_credential),
            selectinload(Agent.tool_links)
            .selectinload(AgentToolLink.tool)
            .selectinload(Tool.credential),
            selectinload(Agent.mcp_tool_links)
            .selectinload(AgentMcpToolLink.mcp_tool)
            .selectinload(McpTool.server)
            .selectinload(McpServer.credential),
            selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
        ),
    ]


async def get_owned_conversation_with_agent(
    db: AsyncSession, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation | None:
    """Single SELECT joining ``conversations ⨝ agents on user_id`` + agent
    runtime eager-loads (model / llm_credential / tool_links / mcp_tool_links
    / skill_links). 결과 ``conv.agent`` 는 별도 query 없이 hydrated.

    ``_resolve_agent_context`` 의 conv lookup + ``get_agent_with_tools`` 두
    round-trip 을 하나로 축소 (W3-out 트랙 종료 retrospective MED follow-up).
    runtime relations 의 selectin chain 자체는 동일하게 발사되므로 SELECT
    수는 (2 + N) → (1 + N) — N=5 (model, llm_credential, tool_links, mcp_tool
    _links, skill_links) 기준 약 14% 절감.

    ``Model.default_credential`` 관계는 의도적으로 chain 에서 제외한다 —
    ``credential_resolution`` 이 FK (``default_credential_id``) 만 읽고 tier 2
    fallback 시 ownership 검증을 위해 ``credential_service.get_for_user`` 로
    별도 fetch 하므로, eager-load 결과는 사용처가 없다.

    Returns ``None`` when the conversation doesn't exist *or* belongs to
    another user — caller should map both to a single 404 (rules/security.md
    enumeration oracle, ``get_owned_conversation`` 와 동일 contract).
    """
    result = await db.execute(
        select(Conversation)
        .join(Agent, Conversation.agent_id == Agent.id)
        .where(Conversation.id == conversation_id, Agent.user_id == user_id)
        .options(
            contains_eager(Conversation.agent).options(
                *_agent_runtime_load_options(),
            )
        )
    )
    return result.unique().scalar_one_or_none()


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
        .options(*_agent_runtime_load_options())
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
    identity: AgentRunIdentity | None = None,
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
    credential_cache: dict[uuid.UUID, dict[str, Any] | None] = {}
    credential_subject_user_id = (
        identity.credential_subject_user_id if identity is not None else agent.user_id
    )
    runtime_actor_user_id = (
        identity.caller_user_id or identity.agent_owner_user_id
        if identity is not None
        else agent.user_id
    )

    async def decrypt_cached(credential: Any) -> dict[str, Any] | None:
        if (
            credential.user_id != credential_subject_user_id
            or bool(getattr(credential, "is_system", False)) is True
        ):
            raise ValidationError(
                "CREDENTIAL_SUBJECT_MISMATCH",
                "credential is not available for this agent run identity",
            )
        cached = credential_cache.get(credential.id)
        if credential.id in credential_cache:
            return cached
        try:
            cached = await credential_service.decrypt_with_external(credential.data_encrypted)
        except Exception:  # noqa: BLE001 — surface as missing creds, never crash chat
            logger.exception(
                "credential decryption failed for credential %s",
                credential.id,
            )
            cached = None
        credential_cache[credential.id] = cached
        return cached

    for link in agent.tool_links:
        tool = link.tool
        if tool is None or not tool.enabled:
            continue

        credentials: dict[str, Any] | None = None
        credential = getattr(tool, "credential", None)
        if credential is not None:
            credentials = await decrypt_cached(credential)

        configs.append(
            {
                "tool_id": str(tool.id),
                "definition_key": tool.definition_key,
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters or {}),
                "credentials": credentials,
                "credential_id": (str(tool.credential_id) if tool.credential_id else None),
                # Hook-framework correlation — wire down to ``tool_factory``.
                "user_id": str(runtime_actor_user_id),
                "agent_id": str(agent.id),
                "credential_subject_user_id": str(credential_subject_user_id),
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
        mcp_headers: dict[str, str] = dict(server.headers or {})
        if server.credential is not None:
            if db is not None:
                resolved_auth = await resolve_mcp_auth(
                    db,
                    credential_id=server.credential_id,
                    user_id=credential_subject_user_id,
                    static_headers=dict(server.headers or {}),
                )
                if resolved_auth.error:
                    if resolved_auth.status == "credential_not_found":
                        raise ValidationError(
                            "CREDENTIAL_SUBJECT_MISMATCH",
                            "credential is not available for this agent run identity",
                        )
                    raise ValidationError(
                        "MCP_CREDENTIAL_AUTH_NEEDED",
                        resolved_auth.error,
                    )
                mcp_credentials = resolved_auth.credentials
                mcp_headers = resolved_auth.headers
            else:
                mcp_credentials = await decrypt_cached(server.credential)
                mcp_headers = build_headers(dict(server.headers or {}), mcp_credentials)

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
                "mcp_transport_headers": mcp_headers,
                "credentials": mcp_credentials,
                "user_id": str(runtime_actor_user_id),
                "agent_id": str(agent.id),
                "credential_subject_user_id": str(credential_subject_user_id),
            }
        )

    return configs


async def trigger_blocked_tools_for_agent_tree(
    agent: Agent,
    *,
    db: AsyncSession,
) -> list[Any]:
    """Return trigger-unsafe capabilities for parent plus one-hop children."""

    blocked = trigger_blocked_tools(
        await build_tools_config(agent, db=db, conversation_id=None),
        has_agent_skills=bool(build_agent_skills(agent)),
    )
    for link in agent.sub_agent_links:
        child = link.sub_agent
        if child is None:
            continue
        child_tools_config = await build_tools_config(
            child,
            db=db,
            conversation_id=None,
        )
        blocked.extend(
            trigger_blocked_tools(
                child_tools_config,
                has_agent_skills=bool(build_agent_skills(child)),
            )
        )
    return blocked
