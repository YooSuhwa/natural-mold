"""Validate loaded private capabilities without resolving any credential values."""

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


def _require_private_credential(credential: Credential, context: OrgContext) -> None:
    if credential.is_system or (credential.user_id, credential.org_id, credential.tenant_id) != (
        context.user_id,
        context.org_id,
        context.tenant_id,
    ):
        raise credential_not_found()


def require_agent_capabilities(agent: Agent, context: OrgContext) -> None:
    """Inspect one loaded Agent and capability references, without decryption."""
    if (agent.user_id, agent.org_id, agent.tenant_id) != (
        context.user_id,
        context.org_id,
        context.tenant_id,
    ):
        raise agent_not_found()
    if agent.llm_credential is not None:
        _require_private_credential(agent.llm_credential, context)
    for link in agent.skill_links:
        skill = link.skill
        if skill is None:
            continue
        private = (skill.user_id, skill.org_id, skill.tenant_id) == (
            context.user_id,
            context.org_id,
            context.tenant_id,
        )
        platform = skill.is_system and skill.org_id is None and skill.tenant_id is None
        if not (private or platform):
            raise skill_not_found()
    for link in agent.tool_links:
        tool = link.tool
        if tool is None or not tool.enabled:
            continue
        private = (tool.user_id, tool.org_id, tool.tenant_id) == (
            context.user_id,
            context.org_id,
            context.tenant_id,
        )
        platform = tool.is_system and (
            tool.user_id is None and tool.org_id is None and tool.tenant_id is None
        )
        if not (private or platform):
            raise tool_not_found()
        if tool.credential is not None:
            _require_private_credential(tool.credential, context)
    for link in agent.mcp_tool_links:
        tool = link.mcp_tool
        if tool is None or not tool.enabled:
            continue
        server = tool.server
        if server is None:
            continue
        private = (server.user_id, server.org_id, server.tenant_id) == (
            context.user_id,
            context.org_id,
            context.tenant_id,
        )
        platform = server.is_system and server.org_id is None and server.tenant_id is None
        if not (private or platform):
            raise mcp_server_not_found()
        if server.credential is not None:
            _require_private_credential(server.credential, context)


def require_runtime_capability_tree(agent: Agent, context: OrgContext) -> None:
    """Validate the supported one-hop runtime tree before resolving any node."""
    require_agent_capabilities(agent, context)
    for link in agent.sub_agent_links:
        if link.sub_agent is not None:
            require_agent_capabilities(link.sub_agent, context)
