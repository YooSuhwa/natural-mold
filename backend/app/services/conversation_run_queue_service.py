from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import conversation_run_service
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_run_queue_claim import (
    ClaimedConversationInput,
    claim_next_input,
)
from app.services.conversation_run_queue_mutations import (
    delete_pending_input,
    edit_pending_input,
    list_inputs,
    lock_owned_conversation,
    pause_queue,
    queue_input_not_found,
    reorder_pending_inputs,
    require_queue_executable,
    resume_queue,
)
from app.services.conversation_run_queue_payload import JsonValue, validate_queue_input_payload
from app.services.conversation_run_queue_persistence import (
    EnqueuedConversationInput,
    QueueInputSource,
    _conflict,
    enqueue_input,
    enqueue_input_with_result,
    persist_direct_input,
)
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy

__all__ = [
    "ClaimedConversationInput",
    "EnqueuedConversationInput",
    "claim_next_input",
    "delete_pending_input",
    "edit_pending_input",
    "enqueue_input",
    "enqueue_input_with_result",
    "list_inputs",
    "lock_owned_conversation",
    "pause_queue",
    "persist_direct_input",
    "queue_input_not_found",
    "reorder_pending_inputs",
    "resume_queue",
]
__all__ += ["AsyncSession", "ConversationPolicyTarget", "ConversationRun", "ConversationRunInput"]
__all__ += ["HTTPException", "JsonValue", "OrgContext", "OwnedExecutionRequest", "QueueInputSource"]
__all__ += ["_conflict", "accept_owned_execution", "conversation_run_service", "func", "select"]
__all__ += ["ensure_conversation_runtime_policy", "require_queue_executable", "utc_now_naive"]
__all__ += ["validate_queue_input_payload"]
