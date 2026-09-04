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
from app.models.agent import AGENT_RUNTIME_PROFILE_SKILL_BUILDER, Agent
from app.models.agent_api import AgentApiRun
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun


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
    db: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    current_trigger_run_id: uuid.UUID | None = None,
) -> tuple[Conversation, Agent, ResolvedRuntimePolicy]:
    """Lock and initialize one conversation snapshot using first-writer-wins semantics."""
    result = await db.execute(
        select(Conversation, Agent)
        .join(Agent, Agent.id == Conversation.agent_id)
        .where(Conversation.id == conversation_id)
        .with_for_update(of=(Conversation, Agent))
    )
    row = result.one_or_none()
    if row is None:
        raise LookupError("conversation runtime policy owner not found")
    conversation, agent = row
    existing = snapshot_from_conversation(conversation)
    if existing is not None:
        return conversation, agent, existing

    prior_execution = await _has_prior_execution(db, conversation.id, current_trigger_run_id)
    if agent.runtime_profile == AGENT_RUNTIME_PROFILE_SKILL_BUILDER:
        resolved = SKILL_BUILDER_RUNTIME_POLICY
    elif prior_execution:
        resolved = LEGACY_RUNTIME_POLICY
    else:
        try:
            resolved = resolve_runtime_policy(agent.runtime_policy)
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
