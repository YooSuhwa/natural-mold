"""Live Agent lifecycle checks for MCP App bindings."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.org_context import OrgContext
from app.exceptions import AppError


@dataclass(frozen=True, slots=True)
class McpAppsError(Exception):
    code: str
    message: str
    status_code: int

    def __str__(self) -> str:
        return self.message


async def require_live_agent_tree(
    db: AsyncSession,
    agent_id: uuid.UUID | None,
    context: OrgContext,
) -> uuid.UUID:
    """Reject retained or archived execution contexts before live MCP lookups."""
    if agent_id is None:
        raise McpAppsError("mcp_app_not_found", "MCP App context was not found", 404)
    try:
        await load_agent_tree_for_execution(db, AgentExecutionTarget(agent_id, context))
    except AppError as error:
        if error.code == "AGENT_ARCHIVED":
            raise McpAppsError(
                "mcp_app_binding_revoked",
                "MCP App binding is no longer active",
                403,
            ) from error
        raise McpAppsError("mcp_app_not_found", "MCP App context was not found", 404) from error
    return agent_id
