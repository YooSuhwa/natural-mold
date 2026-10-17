"""Trigger execution block 2: skip gates, run identity and conversation/policy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import (
    AgentIdentityError,
    AgentRunIdentity,
    AgentRunSource,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.agent_runtime.runtime_policy import ResolvedRuntimePolicy, RuntimePolicySnapshotError
from app.agent_runtime.trigger_execution_claim import TriggerClaim
from app.authz.broken_links import blocked_reason
from app.authz.org_context import OrgContext
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import chat_service, trigger_service
from app.tools.risk import format_trigger_block_reason

logger = logging.getLogger("app.agent_runtime.trigger_executor")


@dataclass(frozen=True, slots=True)
class TriggerOutcome:
    """Early ``execute_trigger`` return value."""

    run: AgentTriggerRun | None


@dataclass(frozen=True, slots=True)
class TriggerMaterialized:
    trigger: AgentTrigger
    run: AgentTriggerRun
    agent: Agent
    context: OrgContext
    identity: AgentRunIdentity
    effective_prompt: str
    agent_skills: list[dict[str, Any]]
    tools_config: list[dict[str, Any]]
    conversation: Conversation
    runtime_policy: ResolvedRuntimePolicy


async def materialize_trigger_run(
    db: AsyncSession, trigger_id: str, claim: TriggerClaim, *, force: bool
) -> TriggerMaterialized | TriggerOutcome:
    trigger, run, agent, context = claim.trigger, claim.run, claim.agent, claim.context
    blocked_tools, tools_config = claim.blocked_tools, claim.tools_config

    if trigger.status != "active" and not force:
        logger.info("Trigger %s skipped (inactive)", trigger_id)
        await trigger_service.finish_trigger_run(
            db,
            trigger=trigger,
            run=run,
            conversation=None,
            status="skipped",
            error_message="trigger is not active",
        )
        await db.refresh(run)
        return TriggerOutcome(run)

    if trigger.max_runs is not None and trigger.run_count >= trigger.max_runs:
        logger.info("Trigger %s skipped (max_runs reached)", trigger_id)
        trigger.status = "completed"
        await trigger_service.finish_trigger_run(
            db,
            trigger=trigger,
            run=run,
            conversation=None,
            status="skipped",
            error_message="max_runs reached",
        )
        await db.refresh(run)
        return TriggerOutcome(run)

    if trigger.end_at is not None and trigger.end_at <= datetime.now(UTC).replace(tzinfo=None):
        logger.info("Trigger %s skipped (end_at reached)", trigger_id)
        trigger.status = "completed"
        await trigger_service.finish_trigger_run(
            db,
            trigger=trigger,
            run=run,
            conversation=None,
            status="skipped",
            error_message="end_at reached",
        )
        await db.refresh(run)
        return TriggerOutcome(run)

    if claim.blocked_links:  # F-03 (10.6): a required link is broken
        reason = blocked_reason(claim.blocked_links)
        logger.warning("Trigger %s blocked: %s", trigger_id, reason)
        await trigger_service.finish_trigger_run(
            db, trigger=trigger, run=run, conversation=None, status="failed", error_message=reason
        )
        await db.refresh(run)
        return TriggerOutcome(run)

    if agent.model is None:
        logger.warning("Trigger %s: agent has no model bound — skipping run", trigger_id)
        await trigger_service.finish_trigger_run(
            db,
            trigger=trigger,
            run=run,
            conversation=None,
            status="failed",
            error_message="agent has no model bound",
        )
        await db.refresh(run)
        return TriggerOutcome(run)

    try:
        identity = resolve_agent_run_identity(
            agent_id=agent.id,
            agent_owner_user_id=agent.user_id,
            runtime_name=agent.runtime_name or make_agent_runtime_name(agent.id),
            identity_mode=agent.identity_mode,
            source=AgentRunSource.TRIGGER,
            caller_user_id=None,
        )
    except AgentIdentityError as exc:
        logger.warning("Trigger %s blocked by identity policy: %s", trigger_id, exc)
        await trigger_service.finish_trigger_run(
            db,
            trigger=trigger,
            run=run,
            conversation=None,
            status="failed",
            error_message=str(exc),
        )
        await db.refresh(run)
        return TriggerOutcome(run)

    run.identity_mode = identity.identity_mode
    run.agent_runtime_name = identity.runtime_name
    run.credential_subject_user_id = identity.credential_subject_user_id
    await db.flush()

    effective_prompt = chat_service.build_effective_prompt(agent)
    agent_skills = chat_service.build_agent_skills(agent, context)
    if blocked_tools:
        reason = format_trigger_block_reason(blocked_tools)
        logger.warning("Trigger %s blocked: %s", trigger_id, reason)
        await trigger_service.finish_trigger_run(
            db,
            trigger=trigger,
            run=run,
            conversation=None,
            status="failed",
            error_message=reason,
        )
        await db.refresh(run)
        return TriggerOutcome(run)

    conversation = await trigger_service.resolve_schedule_conversation(db, trigger, context)
    await db.flush()
    agent = await chat_service.get_agent_with_tools(db, trigger.agent_id, context)
    if agent is None:
        return TriggerOutcome(None)
    try:
        runtime_policy = await trigger_service.ensure_schedule_conversation_runtime_policy(
            db,
            ConversationPolicyTarget(conversation.id, context, run.id),
        )
    except RuntimePolicySnapshotError as exc:
        await trigger_service.finish_trigger_run(
            db,
            trigger=trigger,
            run=run,
            conversation=conversation,
            status="failed",
            error_message=exc.code,
            thread_id=str(conversation.id),
        )
        await db.refresh(run)
        return TriggerOutcome(run)
    return TriggerMaterialized(
        trigger,
        run,
        agent,
        context,
        identity,
        effective_prompt,
        agent_skills,
        tools_config,
        conversation,
        runtime_policy,
    )
