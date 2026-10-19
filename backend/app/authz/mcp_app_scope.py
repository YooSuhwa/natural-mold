"""Persisted MCP App runs inherit an exact private Conversation and Agent scope.

The binding stays private (plan 9.3); the MCP tool it reaches is shared, so every
command also needs the actor's ``can_invoke`` on that tool (D-08). The process
service comes from ``runtime_authz()``; legacy answers with the existing linked-tool
lookup (allow), shadow samples it, enforce asks OpenFGA.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.authz import runtime as authz_runtime
from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.scoping import private_scope_predicate, resource_candidates
from app.authz.service_types import Verdict
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation_run import ConversationRun
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool


@dataclass(frozen=True, slots=True)
class McpAppRunTarget:
    conversation_id: UUID
    run_id: UUID


@dataclass(frozen=True, slots=True)
class McpAppBindingTarget:
    conversation_id: UUID
    run_id: UUID
    tool_call_id: str


def private_mcp_app_binding_query(
    target: McpAppBindingTarget, context: OrgContext
) -> Select[tuple[McpAppInvocationBinding]]:
    return (
        select(McpAppInvocationBinding)
        .execution_options(autoflush=False, populate_existing=True)
        .where(
            McpAppInvocationBinding.conversation_id == target.conversation_id,
            McpAppInvocationBinding.run_id == target.run_id,
            McpAppInvocationBinding.tool_call_id == target.tool_call_id,
            McpAppInvocationBinding.user_id == context.user_id,
        )
    )


async def load_private_mcp_app_run(
    db: AsyncSession, target: McpAppRunTarget, context: OrgContext
) -> ConversationRun | None:
    with db.no_autoflush:
        try:
            conversation = await require_private_conversation(db, target.conversation_id, context)
        except AppError:
            return None
        if conversation.agent_id is None:
            return None
        agent_id = await db.scalar(
            select(Agent.id).where(
                Agent.id == conversation.agent_id, private_scope_predicate(Agent, context)
            )
        )
        if agent_id is None:
            return None
        return await db.scalar(
            select(ConversationRun).where(
                ConversationRun.id == target.run_id,
                ConversationRun.conversation_id == conversation.id,
                ConversationRun.agent_id == agent_id,
                ConversationRun.user_id == context.user_id,
            )
        )


async def actor_can_invoke(context: OrgContext, tool_id: UUID) -> bool:
    """``mcp_tool#can_invoke`` for a tool the existing linked-tool lookup already found.

    That lookup keeps the server inside the caller's organization (or the platform),
    so the decision is made in the caller's organization.
    """
    try:
        await authz_runtime.runtime_authz().require(
            Principal.for_user(context),
            "can_invoke",
            f"mcp_tool:{tool_id}",
            min_visible="can_invoke",
            resource_org_id=context.org_id,
            legacy=Verdict.ALLOW,
        )
    except AppError as denied:
        if denied.status in (403, 404):
            return False
        raise
    return True


async def linked_tool(
    db: AsyncSession,
    *,
    agent_id: UUID,
    server_id: UUID,
    tool_id: UUID | None,
    tool_name: str,
    context: OrgContext,
) -> McpTool | None:
    """An enabled tool linked to the caller's private agent, if the caller may invoke it."""
    stmt = (
        select(McpTool)
        .join(AgentMcpToolLink, AgentMcpToolLink.mcp_tool_id == McpTool.id)
        .join(Agent, Agent.id == AgentMcpToolLink.agent_id)
        .join(McpServer, McpServer.id == McpTool.server_id)
        .where(
            AgentMcpToolLink.agent_id == agent_id,
            McpTool.server_id == server_id,
            McpTool.name == tool_name,
            McpTool.enabled.is_(True),
            private_scope_predicate(Agent, context),
            Agent.archived_at.is_(None),
            McpServer.status != "disabled",
        )
    )
    stmt = resource_candidates(stmt, McpServer, context)
    if tool_id is not None:
        stmt = stmt.where(McpTool.id == tool_id)
    tool = await db.scalar(stmt.execution_options(populate_existing=True, autoflush=False))
    return tool if tool is not None and await actor_can_invoke(context, tool.id) else None
