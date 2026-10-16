"""Trigger service compatibility facade.

Schedule normalization, scheduler sync and run lifecycle live in ``trigger_schedule_service``,
trigger create/update/delete in ``trigger_control_service`` and the schedule
conversation runtime-policy/resolve seams in ``trigger_conversation_runtime``.
"""

from __future__ import annotations

import uuid
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import ResolvedRuntimePolicy
from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.org_context import OrgContext
from app.authz.private_actor import (
    PrivateResourceActor,
    require_private_actor,
    resolve_private_actor,
)
from app.authz.scoping import private_scope_predicate, private_scoped
from app.error_codes import conversation_not_found, trigger_not_found
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.schemas.trigger import TriggerCreate, TriggerUpdate
from app.services.agent_execution_owner import (
    ExecutionEnvelopeRef,
    OwnedExecutionRequest,
    accept_owned_execution,
)
from app.services.agent_execution_runtime import RuntimeOwnershipRequest, run_owned_execution
from app.services.conversation_runtime_policy import (
    ensure_conversation_runtime_policy as _ensure_conversation_runtime_policy,
)
from app.services.prepared_trigger_entry import (
    run_prepared_trigger as run_prepared_trigger_execution,
)
from app.services.prepared_trigger_resolver import (
    PreparedTriggerTarget,
    PreparedTriggerTerminalError,
)
from app.services.trigger_control_service import (
    _ensure_agent_fixed_for_trigger,
    _validate_target_conversation,
    create_trigger,
    delete_trigger,
    get_trigger,
    update_trigger,
)
from app.services.trigger_conversation_runtime import (
    ensure_schedule_conversation_runtime_policy,
    resolve_schedule_conversation,
)
from app.services.trigger_schedule_service import (
    DEFAULT_CONVERSATION_POLICY,
    DEFAULT_TIMEZONE,
    REQUIRES_FIXED_IDENTITY_MESSAGE,
    VALID_CONVERSATION_POLICIES,
    VALID_STATUSES,
    VALID_TRIGGER_TYPES,
    TriggerRunInFlightError,
    _default_name,
    _normalize_optional_datetime,
    _now,
    _parse_datetime,
    _validate_conversation_policy,
    _validate_positive_optional,
    find_in_flight_run,
    finish_trigger_run,
    list_trigger_runs,
    normalize_schedule_config,
    refresh_next_run_at,
    start_trigger_run,
    sync_scheduler_for_trigger,
)


def _serialize_trigger(
    trigger: AgentTrigger,
    *,
    agent_name: str | None = None,
    conversation_title: str | None = None,
    conversation_unread_count: int = 0,
) -> dict[str, Any]:
    return {
        "id": trigger.id,
        "agent_id": trigger.agent_id,
        "name": trigger.name,
        "trigger_type": trigger.trigger_type,
        "schedule_config": trigger.schedule_config,
        "input_message": trigger.input_message,
        "timezone": trigger.timezone,
        "conversation_policy": trigger.conversation_policy,
        "schedule_conversation_id": trigger.schedule_conversation_id,
        "target_conversation_id": trigger.target_conversation_id,
        "status": trigger.status,
        "last_run_at": trigger.last_run_at,
        "next_run_at": trigger.next_run_at,
        "last_status": trigger.last_status,
        "last_error": trigger.last_error,
        "run_count": trigger.run_count,
        "failure_count": trigger.failure_count,
        "max_runs": trigger.max_runs,
        "end_at": trigger.end_at,
        "auto_pause_after_failures": trigger.auto_pause_after_failures,
        "created_at": trigger.created_at,
        "updated_at": trigger.updated_at,
        "agent_name": agent_name,
        "schedule_conversation_title": conversation_title,
        "schedule_conversation_unread_count": conversation_unread_count,
    }


async def get_owned_agent(
    db: AsyncSession, agent_id: uuid.UUID, context: OrgContext
) -> Agent | None:
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, private_scope_predicate(Agent, context))
    )
    return result.scalar_one_or_none()


async def list_triggers(
    db: AsyncSession, agent_id: uuid.UUID, context: OrgContext
) -> list[dict[str, Any]]:
    await require_private_actor(db, context)
    result = await db.execute(
        select(AgentTrigger, Agent.name, Conversation.title, Conversation.unread_count)
        .join(
            Agent, and_(Agent.id == AgentTrigger.agent_id, private_scope_predicate(Agent, context))
        )
        .outerjoin(
            Conversation,
            and_(
                Conversation.id == AgentTrigger.schedule_conversation_id,
                Conversation.agent_id == AgentTrigger.agent_id,
                private_scope_predicate(Conversation, context),
            ),
        )
        .where(AgentTrigger.agent_id == agent_id, private_scope_predicate(AgentTrigger, context))
        .order_by(AgentTrigger.created_at.desc())
    )
    return [
        _serialize_trigger(
            trigger,
            agent_name=agent_name,
            conversation_title=conversation_title,
            conversation_unread_count=conversation_unread_count or 0,
        )
        for trigger, agent_name, conversation_title, conversation_unread_count in result.all()
    ]


async def list_user_triggers(db: AsyncSession, context: OrgContext) -> list[dict[str, Any]]:
    await require_private_actor(db, context)
    result = await db.execute(
        select(AgentTrigger, Agent.name, Conversation.title, Conversation.unread_count)
        .join(
            Agent, and_(Agent.id == AgentTrigger.agent_id, private_scope_predicate(Agent, context))
        )
        .outerjoin(
            Conversation,
            and_(
                Conversation.id == AgentTrigger.schedule_conversation_id,
                Conversation.agent_id == AgentTrigger.agent_id,
                private_scope_predicate(Conversation, context),
            ),
        )
        .where(private_scope_predicate(AgentTrigger, context))
        .order_by(AgentTrigger.created_at.desc())
    )
    return [
        _serialize_trigger(
            trigger,
            agent_name=agent_name,
            conversation_title=conversation_title,
            conversation_unread_count=conversation_unread_count or 0,
        )
        for trigger, agent_name, conversation_title, conversation_unread_count in result.all()
    ]


async def schedule_summary(db: AsyncSession, context: OrgContext) -> dict[str, int]:
    rows = await list_user_triggers(db, context)
    return {
        "total_unread": sum(int(row["schedule_conversation_unread_count"] or 0) for row in rows),
        "active_count": sum(1 for row in rows if row["status"] == "active"),
    }


__all__ = [
    "ExecutionEnvelopeRef",
    "OwnedExecutionRequest",
    "PreparedTriggerTarget",
    "PreparedTriggerTerminalError",
    "RuntimeOwnershipRequest",
    "accept_owned_execution",
    "run_owned_execution",
    "run_prepared_trigger_execution",
]
__all__ += ["CronTrigger", "ResolvedRuntimePolicy", "AgentExecutionTarget"]
__all__ += ["load_agent_tree_for_execution", "private_scoped", "conversation_not_found"]
__all__ += ["trigger_not_found", "AGENT_RUNTIME_PROFILE_STANDARD", "AgentTriggerRun"]
__all__ += ["ConversationPolicyTarget", "TriggerCreate", "TriggerUpdate"]
__all__ += ["_ensure_conversation_runtime_policy", "_ensure_agent_fixed_for_trigger"]
__all__ += ["_validate_target_conversation", "create_trigger", "delete_trigger", "get_trigger"]
__all__ += ["update_trigger", "ensure_schedule_conversation_runtime_policy"]
__all__ += ["resolve_schedule_conversation", "DEFAULT_CONVERSATION_POLICY", "DEFAULT_TIMEZONE"]
__all__ += ["REQUIRES_FIXED_IDENTITY_MESSAGE", "VALID_CONVERSATION_POLICIES", "VALID_STATUSES"]
__all__ += ["VALID_TRIGGER_TYPES", "TriggerRunInFlightError", "_default_name"]
__all__ += ["_normalize_optional_datetime", "_now", "_parse_datetime"]
__all__ += ["_validate_conversation_policy", "_validate_positive_optional", "find_in_flight_run"]
__all__ += ["finish_trigger_run", "list_trigger_runs", "normalize_schedule_config"]
__all__ += ["refresh_next_run_at", "start_trigger_run", "sync_scheduler_for_trigger"]
