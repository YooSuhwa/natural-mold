from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.exceptions import NotFoundError, ValidationError
from app.scheduler import add_trigger_job, pause_trigger_job, remove_trigger_job
from app.schemas.trigger import TriggerCreate, TriggerResponse, TriggerUpdate
from app.services import trigger_service

router = APIRouter(prefix="/api/agents/{agent_id}/triggers", tags=["triggers"])


@router.get("", response_model=list[TriggerResponse])
async def list_triggers(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await trigger_service.list_triggers(db, agent_id)


@router.post("", response_model=TriggerResponse, status_code=201)
async def create_trigger(
    agent_id: uuid.UUID,
    data: TriggerCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    # Validate trigger type
    if data.trigger_type not in ("interval", "cron"):
        raise ValidationError(
            "INVALID_TRIGGER_TYPE", "trigger_type은 'interval' 또는 'cron'이어야 합니다"
        )

    if data.trigger_type == "interval":
        minutes = data.schedule_config.get("interval_minutes")
        if not minutes or not isinstance(minutes, (int, float)) or minutes < 1:
            raise ValidationError(
                "INVALID_SCHEDULE_CONFIG",
                "interval은 schedule_config.interval_minutes >= 1이 필요합니다",
            )

    trigger = await trigger_service.create_trigger(db, agent_id, user.id, data)

    # Register job with scheduler
    add_trigger_job(trigger.id, trigger.trigger_type, trigger.schedule_config)

    return trigger


@router.put("/{trigger_id}", response_model=TriggerResponse)
async def update_trigger(
    agent_id: uuid.UUID,
    trigger_id: uuid.UUID,
    data: TriggerUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    trigger = await trigger_service.get_trigger(db, trigger_id, user.id)
    if not trigger or trigger.agent_id != agent_id:
        raise NotFoundError("TRIGGER_NOT_FOUND", "트리거를 찾을 수 없습니다")

    trigger = await trigger_service.update_trigger(db, trigger, data)

    # Update scheduler job
    if data.status == "paused":
        pause_trigger_job(trigger.id)
    elif data.status == "active":
        # Re-register with possibly updated schedule
        remove_trigger_job(trigger.id)
        add_trigger_job(trigger.id, trigger.trigger_type, trigger.schedule_config)
    elif data.trigger_type or data.schedule_config:
        # Schedule changed, re-register
        remove_trigger_job(trigger.id)
        if trigger.status == "active":
            add_trigger_job(trigger.id, trigger.trigger_type, trigger.schedule_config)

    return trigger


@router.delete("/{trigger_id}", status_code=204)
async def delete_trigger(
    agent_id: uuid.UUID,
    trigger_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    trigger = await trigger_service.get_trigger(db, trigger_id, user.id)
    if not trigger or trigger.agent_id != agent_id:
        raise NotFoundError("TRIGGER_NOT_FOUND", "트리거를 찾을 수 없습니다")

    remove_trigger_job(trigger.id)
    await trigger_service.delete_trigger(db, trigger)
