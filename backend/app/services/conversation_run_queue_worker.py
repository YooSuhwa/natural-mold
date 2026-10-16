from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException
from sqlalchemy import select

from app.agent_runtime.executor import execute_agent_stream_langgraph
from app.agent_runtime.runtime_config import AgentConfig
from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.conversation_input_scope import conversation_input_parent
from app.authz.conversation_scope import resolve_conversation_actor
from app.authz.scoping import private_parent_scoped
from app.database import SessionTenant, platform_session, platform_sessions, tenant_session
from app.database import async_session as database_session
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.user import User
from app.services import conversation_run_queue_service, conversation_run_service
from app.services import conversation_run_queue_worker_metadata as metadata
from app.services.chat_resource_context_integration import (
    reauthorize_resource_context_payload,
)
from app.services.chat_resource_context_sources import ResourceContextScope
from app.services.conversation_run_cancellation_recovery import (
    finalize_workerless_cancel_before_start,
)
from app.services.conversation_run_queue_launch import _fail_claimed_input, launch_claimed_input
from app.services.conversation_run_queue_recovery import recover_conversation_queue
from app.services.conversation_run_worker import (
    RunTaskAlreadyRegisteredError,
    get_run_task_registry,
    start_conversation_run,
)
from app.services.conversation_stream_service import resolve_agent_context

async_session = None
logger = logging.getLogger(__name__)


def _session_factory():
    """Interim: queue claims/recovery keyed by input id until I-06 threads its company."""
    return platform_sessions("conversation_run.queue", maker=async_session or database_session)


async def dispatch_next_for_conversation(
    conversation_id: uuid.UUID,
) -> ConversationRunInput | None:
    """Claim, commit, then launch the next input for one conversation."""
    async with platform_session(
        "conversation_run.queue", maker=async_session or database_session
    ) as db:
        owner = (
            await db.execute(
                select(Conversation.tenant_id, Conversation.user_id).where(
                    Conversation.id == conversation_id
                )
            )
        ).one_or_none()
    if owner is None:
        return None
    owner_id = owner.user_id
    scope = SessionTenant(owner.tenant_id, owner_id)
    async with tenant_session(scope, maker=async_session or database_session) as db:
        try:
            context = await resolve_conversation_actor(db, conversation_id, owner_id)
        except AppError:
            return None
        claimed = await conversation_run_queue_service.claim_next_input(
            db,
            conversation_id=conversation_id,
            context=context,
        )
        if claimed is None:
            return None
        input_id = claimed.input.id
        await db.commit()
    await launch_claimed_input(input_id)
    return claimed.input


__all__ = ["dispatch_next_for_conversation", "launch_claimed_input", "recover_conversation_queue"]
__all__ += ["AgentConfig", "AgentExecutionTarget", "AppError", "ConversationRun", "HTTPException"]
__all__ += ["ConversationRunInput", "ResourceContextScope", "RunTaskAlreadyRegisteredError", "User"]
__all__ += ["_fail_claimed_input", "conversation_input_parent", "conversation_run_service"]
__all__ += ["execute_agent_stream_langgraph", "finalize_workerless_cancel_before_start"]
__all__ += ["get_run_task_registry", "load_agent_tree_for_execution", "metadata"]
__all__ += ["private_parent_scoped", "reauthorize_resource_context_payload"]
__all__ += ["resolve_agent_context", "start_conversation_run"]
