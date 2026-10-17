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

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.agent_runtime.identity import AgentRunIdentity
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.run_agent_scope import require_runnable_agent
from app.authz.runtime_capability_scope import (
    require_agent_capabilities,
    require_runtime_capability_tree,
)
from app.authz.scoping import private_scope_predicate, private_scoped
from app.authz.service import AuthzService
from app.credentials.run_resolution import RunCredentialResolver
from app.credentials.run_subject import subject_for_agent
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink, Tool
from app.outbound.destination import destination_config
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
    db: AsyncSession, conversation_id: uuid.UUID, context: OrgContext
) -> Conversation | None:
    """Single SELECT scoped by private conversation ownership and org/company + agent
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
    with db.no_autoflush:
        await require_private_actor(db, context)
        result = await db.execute(
            private_scoped(
                select(Conversation).where(Conversation.id == conversation_id),
                Conversation,
                context,
            )
            .outerjoin(
                Agent,
                and_(Conversation.agent_id == Agent.id, private_scope_predicate(Agent, context)),
            )
            .execution_options(populate_existing=True)
            .options(
                contains_eager(Conversation.agent).options(
                    *_agent_runtime_load_options(),
                )
            )
        )
        return result.unique().scalar_one_or_none()


async def get_agent_with_tools(
    db: AsyncSession, agent_id: uuid.UUID, context: OrgContext
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

    with db.no_autoflush:
        await require_private_actor(db, context)
        result = await db.execute(
            private_scoped(select(Agent).where(Agent.id == agent_id), Agent, context)
            .execution_options(populate_existing=True, autoflush=False)
            .options(*_agent_runtime_load_options())
        )
        return result.scalar_one_or_none()


def build_effective_prompt(agent: Agent) -> str:
    """Build system prompt — skill bodies are injected by deepagents middleware."""

    return agent.system_prompt


def build_agent_skills(agent: Agent, context: OrgContext) -> list[dict[str, Any]]:
    """Forward the agent's skill links to the runtime descriptor list."""

    require_agent_capabilities(agent, context)
    return build_skills_for_agent(agent.skill_links)


async def build_tools_config(
    agent: Agent,
    *,
    context: OrgContext,
    db: AsyncSession | None = None,
    conversation_id: str | None = None,
    identity: AgentRunIdentity | None = None,
    read_only: bool = False,
    authz: AuthzService | None = None,
) -> list[dict[str, Any]]:
    """Build the runtime tools_config list for an agent.

    Every entry exposes the registry ``definition_key``, the user-supplied
    ``parameters`` and the decrypted ``credentials`` resolved by
    ``RunCredentialResolver`` (11.3, F-05): ``shared`` resources use their owner's
    credential, ``per_user`` resources the run account's connection (caller for
    caller-identity runs, owner for fixed runs). ``read_only`` (response masking)
    resolves without decisions, errors for missing connections or OAuth refresh.
    MCP tool links are emitted in the executor's ``mcp_server_url`` shape.
    """

    await require_runnable_agent(agent, context, identity, authz)
    subject = subject_for_agent(agent, context, identity, read_only=read_only)
    resolver = RunCredentialResolver(db, subject, authz)
    configs: list[dict[str, Any]] = []
    runtime_actor_user_id = (
        identity.caller_user_id or identity.agent_owner_user_id
        if identity is not None
        else agent.user_id
    )
    actor = {
        "user_id": str(runtime_actor_user_id),
        "tenant_id": str(context.tenant_id),
        "agent_id": str(agent.id),
        "credential_subject_user_id": str(subject.account_user_id),
    }

    for link in agent.tool_links:
        tool = link.tool
        if tool is None or not tool.enabled:
            continue
        resolved = await resolver.tool(tool)
        configs.append(
            {
                "tool_id": str(tool.id),
                "definition_key": tool.definition_key,
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters or {}),
                "credentials": resolved.values.reveal() if resolved else None,
                "credential_id": str(resolved.credential_id) if resolved else None,
                "credential_destination": destination_config(resolved, identity, agent.user_id),
                **actor,
            }
        )

    for mcp_link in agent.mcp_tool_links:
        mcp_tool = mcp_link.mcp_tool
        if mcp_tool is None or not mcp_tool.enabled or mcp_tool.server is None:
            continue
        server = mcp_tool.server
        resolution = await resolver.mcp(server)
        if resolution is None:
            continue
        configs.append(
            {
                "tool_id": f"mcp:{mcp_tool.id}",
                "definition_key": "mcp",
                "name": mcp_tool.name,
                "description": mcp_tool.description,
                "parameters": {},
                # _build_mcp_tools branches on these keys (``mcp_server_url``).
                "mcp_server_url": server.url,
                "mcp_server_id": str(server.id),
                "mcp_tool_id": str(mcp_tool.id),
                "mcp_tool_name": mcp_tool.name,
                "org_id": str(context.org_id),
                "mcp_transport_headers": resolution.headers,
                "credentials": resolution.credentials,
                "credential_destination": destination_config(
                    resolution.resolved, identity, agent.user_id
                ),
                "conversation_id": conversation_id,
                **actor,
            }
        )

    resolver.raise_missing()
    return configs


async def trigger_blocked_tools_for_agent_tree(
    agent: Agent,
    *,
    db: AsyncSession,
    context: OrgContext,
) -> list[Any]:
    """Return trigger-unsafe capabilities for parent plus one-hop children."""

    require_runtime_capability_tree(agent, context)
    blocked = trigger_blocked_tools(
        await build_tools_config(agent, context=context, db=db, conversation_id=None),
        has_agent_skills=bool(build_agent_skills(agent, context)),
    )
    for link in agent.sub_agent_links:
        child = link.sub_agent
        if child is None:
            continue
        child_tools_config = await build_tools_config(
            child,
            context=context,
            db=db,
            conversation_id=None,
        )
        blocked.extend(
            trigger_blocked_tools(
                child_tools_config,
                has_agent_skills=bool(build_agent_skills(child, context)),
            )
        )
    return blocked
