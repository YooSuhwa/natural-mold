"""Agent service compatibility facade.

Implementation lives in ``agent_query_service``, ``agent_binding_service``,
``agent_creation_service`` and ``agent_update_service``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_runtime.identity import make_agent_runtime_name, validate_identity_mode
from app.agent_runtime.runtime_policy import runtime_policy_to_json
from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.agent_projection_scope import (
    AgentPresentation,
    load_agent_presentation,
    mcp_link_scope,
    projection_options,
    tool_link_scope,
)
from app.authz.org_context import OrgContext
from app.authz.private_actor import (
    PrivateResourceActor,
    require_private_actor,
    resolve_private_actor,
)
from app.authz.scoping import private_scope_predicate, resource_candidates
from app.error_codes import agent_not_found
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.models.skill import AgentSkillLink
from app.models.template import Template
from app.models.tool import AgentToolLink, Tool
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services.agent_binding_service import (
    _build_tool_links,
    _validate_agent_binding_ids,
    _validate_mcp_tool_ids_owned,
    _validate_model_fallback_ids,
    _validate_skill_ids_owned,
    _validate_sub_agent_ids_owned,
    _validate_tool_ids_owned,
)
from app.services.agent_creation_service import _install_template_skills, create_agent
from app.services.agent_deletion import delete_agent as delete_agent
from app.services.agent_image_paths import build_agent_image_url
from app.services.agent_query_service import (
    _selectin_agent,
    get_agent,
    get_agent_projection,
    list_agent_summaries,
    list_agents,
)
from app.services.agent_update_service import _count_active_triggers, toggle_favorite, update_agent

logger = logging.getLogger(__name__)

__all__ = ["AGENT_RUNTIME_PROFILE_STANDARD", "Agent", "AgentCreate", "AgentExecutionTarget"]
__all__ += ["AgentMcpToolLink", "AgentPresentation", "AgentSkillLink", "AgentSubAgentLink"]
__all__ += ["AgentToolLink", "AgentUpdate", "Any", "AsyncSession", "HTTPException", "McpServer"]
__all__ += ["McpTool", "Model", "OrgContext", "PrivateResourceActor", "Template", "Tool"]
__all__ += ["_build_tool_links", "_count_active_triggers", "_install_template_skills"]
__all__ += ["_selectin_agent", "_validate_agent_binding_ids", "_validate_mcp_tool_ids_owned"]
__all__ += ["_validate_model_fallback_ids", "_validate_skill_ids_owned"]
__all__ += ["_validate_sub_agent_ids_owned", "_validate_tool_ids_owned", "agent_not_found"]
__all__ += ["build_agent_image_url", "cast", "create_agent", "delete_agent", "func", "get_agent"]
__all__ += ["get_agent_projection", "list_agent_summaries", "list_agents"]
__all__ += ["load_agent_presentation", "load_agent_tree_for_execution", "logger"]
__all__ += ["make_agent_runtime_name", "mcp_link_scope", "or_", "private_scope_predicate"]
__all__ += ["projection_options", "require_private_actor", "resolve_private_actor"]
__all__ += ["resource_candidates", "runtime_policy_to_json", "select", "selectinload"]
__all__ += ["toggle_favorite", "tool_link_scope", "update_agent", "uuid", "validate_identity_mode"]
