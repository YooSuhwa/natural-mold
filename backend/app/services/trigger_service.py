from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_trigger import AgentTrigger
from app.schemas.trigger import TriggerCreate, TriggerUpdate


async def create_trigger(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    data: TriggerCreate,
) -> AgentTrigger:
    trigger = AgentTrigger(
        agent_id=agent_id,
        user_id=user_id,
        trigger_type=data.trigger_type,
        schedule_config=data.schedule_config,
        input_message=data.input_message,
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    return trigger


async def list_triggers(db: AsyncSession, agent_id: uuid.UUID) -> list[AgentTrigger]:
    result = await db.execute(
        select(AgentTrigger)
        .where(AgentTrigger.agent_id == agent_id)
        .order_by(AgentTrigger.created_at.desc())
    )
    return list(result.scalars().all())


async def get_trigger(
    db: AsyncSession, trigger_id: uuid.UUID, user_id: uuid.UUID
) -> AgentTrigger | None:
    result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.id == trigger_id,
            AgentTrigger.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def update_trigger(
    db: AsyncSession, trigger: AgentTrigger, data: TriggerUpdate
) -> AgentTrigger:
    if data.trigger_type is not None:
        trigger.trigger_type = data.trigger_type
    if data.schedule_config is not None:
        trigger.schedule_config = data.schedule_config
    if data.input_message is not None:
        trigger.input_message = data.input_message
    if data.status is not None:
        trigger.status = data.status
    await db.commit()
    await db.refresh(trigger)
    return trigger


async def delete_trigger(db: AsyncSession, trigger: AgentTrigger) -> None:
    await db.delete(trigger)
    await db.commit()
