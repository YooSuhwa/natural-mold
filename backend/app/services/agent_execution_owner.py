"""Shared durable ownership for accepted Agent execution work.

Task 10 I4 compatibility facade: acceptance lives in ``agent_execution_acceptance``
and legacy read-only queries in ``agent_execution_legacy_queries``.
"""

from __future__ import annotations

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, noload

from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.org_context import OrgContext
from app.models.agent_api import AgentApiRun
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_service
from app.services.agent_execution_acceptance import (
    AcceptedParticipant,
    EnvelopeKind,
    ExecutionEnvelopeRef,
    ExecutionOwnershipError,
    OwnedExecutionAcceptance,
    OwnedExecutionRequest,
    OwnedExecutionSource,
    _bind_envelope,
    _participant_paths,
    _validated_envelope,
    accept_owned_execution,
)
from app.services.agent_execution_legacy_queries import (
    UnlinkedEnvelopeConflicts,
    active_unlinked_envelopes,
    participant_run_conflicts,
)
from app.services.agent_lifecycle_locks import AgentTreeSnapshot
from app.services.conversation_run_envelope_projection import (
    api_status_for_run,
    project_owned_envelopes,
    trigger_status_for_run,
)
from app.services.conversation_run_ownership_cancellation import (
    CancelIntentRequest,
    ParticipantCancelRequest,
    PreparedCancelIntent,
    notify_local_owner,
    persist_cancel_intent,
    stage_cancel_intent,
    stage_participant_cancel_intents,
)
from app.services.conversation_run_service import RunSource

__all__ = [
    "CancelIntentRequest",
    "ExecutionOwnershipError",
    "ExecutionEnvelopeRef",
    "OwnedExecutionAcceptance",
    "OwnedExecutionRequest",
    "OwnedExecutionSource",
    "ParticipantCancelRequest",
    "PreparedCancelIntent",
    "UnlinkedEnvelopeConflicts",
    "accept_owned_execution",
    "active_unlinked_envelopes",
    "api_status_for_run",
    "notify_local_owner",
    "persist_cancel_intent",
    "project_owned_envelopes",
    "participant_run_conflicts",
    "stage_cancel_intent",
    "stage_participant_cancel_intents",
    "trigger_status_for_run",
]
# Compatibility re-exports: names this module exposed before the I4 split.
__all__ += ["JsonValue", "select", "AsyncSession", "load_only", "noload", "AgentExecutionTarget"]
__all__ += ["OrgContext"]
__all__ += ["load_agent_tree_for_execution", "AgentApiRun", "ConversationRunAgent"]
__all__ += ["AgentTriggerRun", "Conversation", "ConversationRun", "conversation_run_service"]
__all__ += ["AcceptedParticipant", "EnvelopeKind", "_bind_envelope", "_participant_paths"]
__all__ += ["_validated_envelope", "AgentTreeSnapshot", "RunSource"]
