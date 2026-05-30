from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import (
    agent_not_found,
    invalid_schedule_config,
    invalid_trigger_type,
    trigger_not_found,
)
from app.schemas.trigger import (
    TriggerCreate,
    TriggerResponse,
    TriggerRunResponse,
    TriggerSummaryResponse,
    TriggerUpdate,
)
from app.services import trigger_service

router = APIRouter(tags=["triggers"])


def _map_trigger_validation(exc: ValueError) -> None:
    if "trigger_type" in str(exc):
        raise invalid_trigger_type() from exc
    raise invalid_schedule_config() from exc


@router.get("/api/triggers", response_model=list[TriggerResponse])
async def list_all_triggers(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await trigger_service.list_user_triggers(db, user.id)


@router.get("/api/triggers/summary", response_model=TriggerSummaryResponse)
async def trigger_summary(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await trigger_service.schedule_summary(db, user.id)


@router.patch("/api/triggers/{trigger_id}", response_model=TriggerResponse)
async def update_trigger_global(
    trigger_id: uuid.UUID,
    data: TriggerUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    trigger = await trigger_service.get_trigger(db, trigger_id, user.id)
    if not trigger:
        raise trigger_not_found()
    try:
        return await trigger_service.update_trigger(db, trigger, data)
    except ValueError as exc:
        _map_trigger_validation(exc)


@router.delete("/api/triggers/{trigger_id}", status_code=204)
async def delete_trigger_global(
    trigger_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    trigger = await trigger_service.get_trigger(db, trigger_id, user.id)
    if not trigger:
        raise trigger_not_found()
    await trigger_service.delete_trigger(db, trigger)


@router.post("/api/triggers/{trigger_id}/run-now", response_model=TriggerRunResponse)
async def run_trigger_now(
    trigger_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    trigger = await trigger_service.get_trigger(db, trigger_id, user.id)
    if not trigger:
        raise trigger_not_found()

    from app.agent_runtime.trigger_executor import execute_trigger

    run = await execute_trigger(str(trigger_id), force=True)
    if run is None:
        raise trigger_not_found()
    return run


@router.get("/api/triggers/{trigger_id}/runs", response_model=list[TriggerRunResponse])
async def list_trigger_runs(
    trigger_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    trigger = await trigger_service.get_trigger(db, trigger_id, user.id)
    if not trigger:
        raise trigger_not_found()
    return await trigger_service.list_trigger_runs(db, trigger_id, user.id)


@router.get(
    "/api/agents/{agent_id}/triggers",
    response_model=list[TriggerResponse],
)
async def list_agent_triggers(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agent = await trigger_service.get_owned_agent(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    return await trigger_service.list_triggers(db, agent_id, user.id)


@router.post(
    "/api/agents/{agent_id}/triggers",
    response_model=TriggerResponse,
    status_code=201,
)
async def create_trigger(
    agent_id: uuid.UUID,
    data: TriggerCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await trigger_service.get_owned_agent(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    try:
        return await trigger_service.create_trigger(db, agent_id, user.id, data)
    except ValueError as exc:
        _map_trigger_validation(exc)


@router.put(
    "/api/agents/{agent_id}/triggers/{trigger_id}",
    response_model=TriggerResponse,
)
async def update_agent_trigger(
    agent_id: uuid.UUID,
    trigger_id: uuid.UUID,
    data: TriggerUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    trigger = await trigger_service.get_trigger(db, trigger_id, user.id)
    if not trigger or trigger.agent_id != agent_id:
        raise trigger_not_found()
    try:
        return await trigger_service.update_trigger(db, trigger, data)
    except ValueError as exc:
        _map_trigger_validation(exc)


@router.delete(
    "/api/agents/{agent_id}/triggers/{trigger_id}",
    status_code=204,
)
async def delete_agent_trigger(
    agent_id: uuid.UUID,
    trigger_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    trigger = await trigger_service.get_trigger(db, trigger_id, user.id)
    if not trigger or trigger.agent_id != agent_id:
        raise trigger_not_found()
    await trigger_service.delete_trigger(db, trigger)
