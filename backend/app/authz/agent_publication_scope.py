"""Authorize every metadata export target before publishing an Agent blueprint."""

from app.authz.org_context import OrgContext
from app.error_codes import (
    agent_not_found,
    credential_not_found,
    mcp_server_not_found,
    skill_not_found,
    tool_not_found,
)
from app.models.agent import Agent
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.skill import Skill
from app.models.tool import Tool


def _can_export_resource(
    resource: Tool | Skill | McpServer | Credential, context: OrgContext
) -> bool:
    if (resource.user_id, resource.org_id, resource.tenant_id) == (
        context.user_id,
        context.org_id,
        context.tenant_id,
    ):
        return True
    if not (resource.is_system and resource.org_id is None and resource.tenant_id is None):
        return False
    return not isinstance(resource, Tool | Credential) or resource.user_id is None


def _require_credential(credential: Credential | None, context: OrgContext) -> None:
    if credential is not None and not _can_export_resource(credential, context):
        raise credential_not_found()


def require_agent_publication_scope(agent: Agent, context: OrgContext) -> None:
    """Disabled capabilities are exported too; validate all direct metadata targets."""
    _require_credential(agent.llm_credential, context)
    for link in agent.tool_links:
        if not _can_export_resource(link.tool, context):
            raise tool_not_found()
        _require_credential(link.tool.credential, context)
    for link in agent.skill_links:
        if not _can_export_resource(link.skill, context):
            raise skill_not_found()
    for link in agent.mcp_tool_links:
        server = link.mcp_tool.server
        if not _can_export_resource(server, context):
            raise mcp_server_not_found()
        _require_credential(server.credential, context)
    for link in agent.sub_agent_links:
        child = link.sub_agent
        if (child.user_id, child.org_id, child.tenant_id) != (
            context.user_id,
            context.org_id,
            context.tenant_id,
        ):
            raise agent_not_found()
