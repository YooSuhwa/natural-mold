"""Builder 서비스 — 세션 관리, v3 메시지 스트리밍, confirm 로직.

Compatibility facade: implementation lives in ``builder_session_service``,
``builder_resource_resolution`` and ``builder_agent_finalization``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_runtime.identity import AGENT_IDENTITY_PER_USER, validate_identity_mode
from app.agent_runtime.middleware_registry import get_middleware_registry
from app.agent_runtime.streaming import format_sse
from app.authz.builder_scope import resolve_builder_actor
from app.authz.org_context import OrgContext
from app.authz.private_actor import (
    PrivateResourceActor,
    require_private_actor,
    resolve_private_actor,
)
from app.authz.scoping import private_scope_predicate, private_scoped, resource_candidates
from app.database import async_session as async_session_factory
from app.error_codes import session_not_found
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.builder_session import BuilderSession
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import AgentSkillLink, Skill
from app.models.tool import AgentToolLink, Tool
from app.schemas.builder import BuilderStatus
from app.schemas.conversation import Decision
from app.services.builder_agent_finalization import (
    _transfer_builder_image,
    _transfer_builder_image_sync,
    confirm_build,
)
from app.services.builder_resource_resolution import _resolve_tools
from app.services.builder_session_service import (
    StaleInterruptError,
    _get_default_model_name,
    _get_middlewares_catalog,
    claim_for_confirming,
    create_session,
    decisions_to_builder_response,
    get_agent_by_id,
    get_session,
    run_v3_message_stream,
    run_v3_resume_stream,
)
from app.services.model_service import resolve_model
from app.services.tool_service import get_tools_catalog

logger = logging.getLogger(__name__)

__all__ = ["AGENT_IDENTITY_PER_USER", "Agent", "AgentMcpToolLink", "AgentSkillLink"]
__all__ += ["AgentToolLink", "Any", "AppError", "AsyncGenerator", "AsyncSession"]
__all__ += ["BuilderSession", "BuilderStatus", "Decision", "McpServer", "McpTool", "OrgContext"]
__all__ += ["PrivateResourceActor", "Skill", "StaleInterruptError", "Tool"]
__all__ += ["_get_default_model_name", "_get_middlewares_catalog", "_resolve_tools"]
__all__ += ["_transfer_builder_image", "_transfer_builder_image_sync", "async_session_factory"]
__all__ += ["asyncio", "claim_for_confirming", "confirm_build", "create_session"]
__all__ += ["decisions_to_builder_response", "format_sse", "func", "get_agent_by_id"]
__all__ += ["get_middleware_registry", "get_session", "get_tools_catalog", "logger"]
__all__ += ["private_scope_predicate", "private_scoped", "require_private_actor"]
__all__ += ["resolve_builder_actor", "resolve_model", "resolve_private_actor"]
__all__ += ["resource_candidates", "run_v3_message_stream", "run_v3_resume_stream", "select"]
__all__ += ["selectinload", "session_not_found", "update", "uuid", "validate_identity_mode"]
