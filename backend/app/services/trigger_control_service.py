from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scope_predicate, private_scoped
from app.error_codes import trigger_not_found
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.agent_trigger import AgentTrigger
from app.models.conversation import Conversation
from app.schemas.trigger import TriggerCreate, TriggerUpdate
from app.services.trigger_schedule_service import (
    REQUIRES_FIXED_IDENTITY_MESSAGE,
    VALID_STATUSES,
    _default_name,
    _normalize_optional_datetime,
    _validate_conversation_policy,
    _validate_positive_optional,
    normalize_schedule_config,
)


def _trigger_service_attr(name: str) -> Any:
    """Return ``trigger_service.<name>`` as currently bound (tests patch it)."""

    from app.services import trigger_service

    return getattr(trigger_service, name)


async def _ensure_agent_fixed_for_trigger(
    db: AsyncSession,
    agent_id: uuid.UUID,
    context: OrgContext,
) -> Agent:
    executable = await load_agent_tree_for_execution(db, AgentExecutionTarget(agent_id, context))
    agent = next((item for item in executable.agents if item.id == agent_id), None)
    if agent is None:
        raise ValueError("agent not found")
    # 히든 런타임 에이전트(skill builder 등)는 트리거 대상 불가 — 트리거 실행은
    # 빌더 분기·System LLM 재해석을 타지 않아 placeholder 프롬프트가 표준
    # 에이전트로 스케줄 실행된다. not-found와 동일 응답(enumeration-safe).
    if agent.runtime_profile != AGENT_RUNTIME_PROFILE_STANDARD:
        raise ValueError("agent not found")
    if agent.identity_mode != "fixed":
        raise ValueError(REQUIRES_FIXED_IDENTITY_MESSAGE)
    return agent


async def get_trigger(
    db: AsyncSession, trigger_id: uuid.UUID, context: OrgContext
) -> AgentTrigger | None:
    await resolve_private_actor(
        db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
    )
    result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.id == trigger_id,
            private_scope_predicate(AgentTrigger, context),
        )
    )
    return result.scalar_one_or_none()


async def _validate_target_conversation(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    target_conversation_id: uuid.UUID | None,
    conversation_policy: str,
    context: OrgContext,
) -> uuid.UUID | None:
    if conversation_policy != "selected_conversation":
        return None
    if target_conversation_id is None:
        raise ValueError("target_conversation_id is required")
    conversation = await db.scalar(
        private_scoped(
            select(Conversation).where(Conversation.id == target_conversation_id),
            Conversation,
            context,
        )
    )
    if conversation is None or conversation.agent_id != agent_id:
        raise ValueError("target_conversation_id is invalid")
    return target_conversation_id


async def create_trigger(
    db: AsyncSession,
    agent_id: uuid.UUID,
    context: OrgContext,
    data: TriggerCreate,
) -> AgentTrigger:
    await _ensure_agent_fixed_for_trigger(db, agent_id, context)
    schedule_config, timezone = normalize_schedule_config(
        data.trigger_type,
        data.schedule_config,
        data.timezone,
    )
    conversation_policy = _validate_conversation_policy(data.conversation_policy)
    target_conversation_id = await _validate_target_conversation(
        db,
        context=context,
        agent_id=agent_id,
        target_conversation_id=data.target_conversation_id,
        conversation_policy=conversation_policy,
    )
    name = (data.name or "").strip() or _default_name(data.trigger_type, schedule_config)
    trigger = AgentTrigger(
        agent_id=agent_id,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name=name,
        trigger_type=data.trigger_type,
        schedule_config=schedule_config,
        input_message=data.input_message,
        timezone=timezone,
        conversation_policy=conversation_policy,
        target_conversation_id=target_conversation_id,
        max_runs=_validate_positive_optional(data.max_runs, "max_runs"),
        end_at=_normalize_optional_datetime(data.end_at),
        auto_pause_after_failures=_validate_positive_optional(
            data.auto_pause_after_failures,
            "auto_pause_after_failures",
        ),
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    await _trigger_service_attr("sync_scheduler_for_trigger")(db, trigger)
    return trigger


async def update_trigger(
    db: AsyncSession, trigger: AgentTrigger, data: TriggerUpdate, *, context: OrgContext
) -> AgentTrigger:
    scoped = await get_trigger(db, trigger.id, context)
    if scoped is None:
        raise trigger_not_found()
    trigger = scoped
    target_status = data.status if data.status is not None else trigger.status
    if target_status == "active":
        await _ensure_agent_fixed_for_trigger(db, trigger.agent_id, context)
    trigger_type = data.trigger_type or trigger.trigger_type
    timezone = data.timezone or trigger.timezone

    schedule_changed = (
        data.schedule_config is not None
        or data.trigger_type is not None
        or data.timezone is not None
    )
    if schedule_changed:
        schedule_config, timezone = normalize_schedule_config(
            trigger_type,
            data.schedule_config or trigger.schedule_config,
            timezone,
        )
        trigger.trigger_type = trigger_type
        trigger.schedule_config = schedule_config
        trigger.timezone = timezone

    if data.name is not None:
        trigger.name = data.name.strip() or _default_name(
            trigger.trigger_type,
            trigger.schedule_config,
        )
    if data.input_message is not None:
        trigger.input_message = data.input_message
    provided_fields = data.model_fields_set
    if "conversation_policy" in provided_fields or "target_conversation_id" in provided_fields:
        conversation_policy = _validate_conversation_policy(
            data.conversation_policy or trigger.conversation_policy
        )
        target_conversation_id = await _validate_target_conversation(
            db,
            context=context,
            agent_id=trigger.agent_id,
            target_conversation_id=(
                data.target_conversation_id
                if "target_conversation_id" in provided_fields
                else trigger.target_conversation_id
            ),
            conversation_policy=conversation_policy,
        )
        trigger.conversation_policy = conversation_policy
        trigger.target_conversation_id = target_conversation_id
    if data.status is not None:
        if data.status not in VALID_STATUSES:
            raise ValueError("invalid trigger status")
        trigger.status = data.status
    if "max_runs" in provided_fields:
        trigger.max_runs = _validate_positive_optional(data.max_runs, "max_runs")
    if "end_at" in provided_fields:
        trigger.end_at = _normalize_optional_datetime(data.end_at)
    if "auto_pause_after_failures" in provided_fields:
        trigger.auto_pause_after_failures = _validate_positive_optional(
            data.auto_pause_after_failures,
            "auto_pause_after_failures",
        )

    await db.commit()
    await db.refresh(trigger)
    await _trigger_service_attr("sync_scheduler_for_trigger")(db, trigger)
    return trigger


async def delete_trigger(db: AsyncSession, trigger: AgentTrigger, context: OrgContext) -> None:
    scoped = await get_trigger(db, trigger.id, context)
    if scoped is None:
        raise trigger_not_found()
    trigger = scoped
    from app.scheduler import remove_trigger_job

    remove_trigger_job(trigger.id)
    await db.delete(trigger)
    await db.commit()
