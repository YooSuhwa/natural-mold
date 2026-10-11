"""Transactional conversation runtime-policy snapshot ownership."""

from __future__ import annotations

import uuid

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import (
    LEGACY_RUNTIME_POLICY,
    SKILL_BUILDER_RUNTIME_POLICY,
    ResolvedRuntimePolicy,
    resolve_runtime_policy,
    runtime_policy_to_json,
    validate_runtime_policy_snapshot,
)
from app.authz.conversation_scope import lock_private_conversation
from app.authz.scoping import private_scope_predicate
from app.error_codes import agent_not_found
from app.models.agent import AGENT_RUNTIME_PROFILE_SKILL_BUILDER, Agent
from app.models.agent_api import AgentApiRun
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.schemas.conversation_scope import ConversationPolicyTarget


def snapshot_from_conversation(conversation: Conversation) -> ResolvedRuntimePolicy | None:
    """Parse the all-or-none policy tuple stored on a conversation."""
    return validate_runtime_policy_snapshot(
        conversation.runtime_policy_snapshot,
        conversation.runtime_policy_version,
        conversation.runtime_policy_hash,
        conversation.runtime_policy_source,
    )


def _persist_snapshot(
    conversation: Conversation,
    resolved: ResolvedRuntimePolicy,
) -> None:
    conversation.runtime_policy_snapshot = runtime_policy_to_json(resolved.effective)
    conversation.runtime_policy_version = resolved.effective.version
    conversation.runtime_policy_hash = resolved.policy_hash
    conversation.runtime_policy_source = resolved.source


async def _has_prior_execution(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    current_trigger_run_id: uuid.UUID | None,
) -> bool:
    for model in (ConversationRun, AgentApiRun, AgentTriggerRun):
        statement = select(model.id).where(model.conversation_id == conversation_id)
        if model is AgentTriggerRun and current_trigger_run_id is not None:
            statement = statement.where(model.id != current_trigger_run_id)
        prior_id = await db.scalar(statement.limit(1))
        if prior_id is not None:
            return True
    return False


async def ensure_conversation_runtime_policy(
    db: AsyncSession, target: ConversationPolicyTarget
) -> tuple[Conversation, Agent, ResolvedRuntimePolicy]:
    """Initialize one scoped conversation snapshot with first-writer-wins locking."""
    conversation = await lock_private_conversation(db, target.conversation_id, target.context)
    result = await db.execute(
        select(Agent, Agent.runtime_profile, Agent.runtime_policy)
        .where(Agent.id == conversation.agent_id, private_scope_predicate(Agent, target.context))
        .with_for_update(of=Agent)
    )
    row = result.one_or_none()
    if row is None:
        raise agent_not_found()
    agent, runtime_profile, agent_policy = row
    existing = snapshot_from_conversation(conversation)
    if existing is not None:
        return conversation, agent, existing

    prior_execution = await _has_prior_execution(db, conversation.id, target.current_trigger_run_id)
    if runtime_profile == AGENT_RUNTIME_PROFILE_SKILL_BUILDER:
        resolved = SKILL_BUILDER_RUNTIME_POLICY
    elif prior_execution:
        resolved = LEGACY_RUNTIME_POLICY
    else:
        try:
            resolved = resolve_runtime_policy(agent_policy)
        except PydanticValidationError:
            from app.agent_runtime.runtime_policy import RuntimePolicySnapshotError

            raise RuntimePolicySnapshotError from None
    _persist_snapshot(conversation, resolved)
    return conversation, agent, resolved


def copy_snapshot_to_run(
    run: ConversationRun,
    resolved: ResolvedRuntimePolicy,
) -> None:
    """Copy immutable conversation provenance to a new durable run."""
    run.runtime_policy_version = resolved.effective.version
    run.runtime_policy_hash = resolved.policy_hash
    run.runtime_policy_source = resolved.source


def require_parent_snapshot_match(
    parent: ConversationRun,
    resolved: ResolvedRuntimePolicy,
) -> None:
    """Reject resume when parent provenance does not exactly match its conversation."""
    parent_tuple = (
        parent.runtime_policy_version,
        parent.runtime_policy_hash,
        parent.runtime_policy_source,
    )
    expected = (resolved.effective.version, resolved.policy_hash, resolved.source)
    if parent_tuple == (None, None, None) and resolved.source in {
        "legacy_compat",
        "server_owned",
    }:
        parent.runtime_policy_version, parent.runtime_policy_hash, parent.runtime_policy_source = (
            expected
        )
        return
    if parent_tuple != expected:
        from app.agent_runtime.runtime_policy import RuntimePolicySnapshotError

        raise RuntimePolicySnapshotError
