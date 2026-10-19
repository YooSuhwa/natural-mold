"""Agent API runtime compatibility facade.

Nodes live in ``deployment_access``, ``thread_runtime``, ``run_acceptance`` and
``run_execution``; tests patch runtime collaborators on this module, which the
extracted execution module reads at call time.
"""

from __future__ import annotations

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_api.access import authorize_deployment, ensure_key_can_access_deployment
from app.agent_api.dependencies import ApiKeyPrincipal, refresh_api_key_principal
from app.agent_api.deployment_access import resolve_deployment_for_agent_id
from app.agent_api.run_acceptance import (
    _API_MESSAGES,
    AgentApiRunInputPayload,
    create_run_row,
    external_run_id,
    validate_messages,
)
from app.agent_api.run_execution import (
    ApiOwnedExecution,
    build_config_for_run,
    execute_owned_wait,
    mark_run_failed,
    mark_run_succeeded,
    stream_owned_run,
)
from app.agent_api.service import utc_now_naive
from app.agent_api.thread_runtime import (
    create_api_conversation,
    create_thread,
    external_thread_id,
    get_thread_for_public_id,
    thread_to_response,
)
from app.agent_runtime.agent_stream_runner import execute_agent_invoke, execute_agent_stream
from app.agent_runtime.message_utils import RuntimeMessage, is_runtime_message
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.runtime_policy import RuntimePolicySnapshotError
from app.agent_runtime.streaming import StreamErrorRecord
from app.authz.agent_api_scope import (
    private_api_thread_parents_predicate,
    private_deployment_predicate,
)
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scoped
from app.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.models.agent_api import AgentApiRun, AgentApiThread, AgentDeployment
from app.models.conversation import Conversation
from app.schemas.agent_api import AgentRunRequest, AgentThreadCreateRequest, AgentThreadResponse
from app.schemas.conversation_refs import JsonValue
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import chat_service
from app.services.agent_execution_owner import (
    ExecutionEnvelopeRef,
    OwnedExecutionRequest,
    accept_owned_execution,
)
from app.services.agent_execution_runtime import (
    RuntimeOwnershipRequest,
    run_owned_execution,
    stream_owned_execution,
)
from app.services.agent_invocation_service import (
    AgentInvocationPrincipal,
    build_agent_config_for_loaded_agent,
)
from app.services.conversation_runtime_policy import (
    ensure_conversation_runtime_policy,
    snapshot_from_conversation,
)

__all__ = ["ApiOwnedExecution", "AgentApiRunInputPayload", "_API_MESSAGES", "external_run_id"]
__all__ += ["external_thread_id", "resolve_deployment_for_agent_id", "create_api_conversation"]
__all__ += ["create_thread", "thread_to_response", "get_thread_for_public_id", "create_run_row"]
__all__ += ["mark_run_succeeded", "mark_run_failed", "execute_owned_wait", "stream_owned_run"]
__all__ += ["build_config_for_run", "validate_messages"]
__all__ += [
    "TypeAdapter",
    "PydanticValidationError",
    "or_",
    "select",
    "AsyncSession",
    "selectinload",
]
__all__ += ["authorize_deployment", "ensure_key_can_access_deployment", "ApiKeyPrincipal"]
__all__ += ["refresh_api_key_principal", "utc_now_naive", "execute_agent_invoke"]
__all__ += ["execute_agent_stream", "RuntimeMessage", "is_runtime_message", "AgentConfig"]
__all__ += ["RuntimePolicySnapshotError", "StreamErrorRecord"]
__all__ += ["private_api_thread_parents_predicate", "private_deployment_predicate", "OrgContext"]
__all__ += ["private_scoped", "ConflictError", "ForbiddenError", "NotFoundError", "ValidationError"]
__all__ += ["AgentApiRun", "AgentApiThread", "AgentDeployment", "Conversation", "AgentRunRequest"]
__all__ += ["AgentThreadCreateRequest", "AgentThreadResponse", "JsonValue"]
__all__ += ["ConversationPolicyTarget", "chat_service", "ExecutionEnvelopeRef"]
__all__ += ["OwnedExecutionRequest", "accept_owned_execution", "RuntimeOwnershipRequest"]
__all__ += ["run_owned_execution", "stream_owned_execution", "AgentInvocationPrincipal"]
__all__ += ["build_agent_config_for_loaded_agent", "ensure_conversation_runtime_policy"]
__all__ += ["snapshot_from_conversation"]
