from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import (
    agent_identity_requires_fixed,
    agent_not_found,
    invalid_schedule_config,
    invalid_trigger_type,
    trigger_not_found,
)
from app.models.agent_trigger import AgentTrigger
from app.schemas.trigger import (
    TriggerCreate,
    TriggerResponse,
    TriggerRunResponse,
    TriggerSummaryResponse,
    TriggerUpdate,
)
from app.services import audit_service, trigger_service

router = APIRouter(tags=["triggers"])


def _trigger_metadata(trigger: AgentTrigger) -> dict[str, object]:
    return {
        "agent_id": str(trigger.agent_id),
        "trigger_type": trigger.trigger_type,
        "schedule_config_keys": sorted((trigger.schedule_config or {}).keys()),
        "timezone": trigger.timezone,
        "conversation_policy": trigger.conversation_policy,
        "status": trigger.status,
        "input_message_length": len(trigger.input_message or ""),
        "max_runs": trigger.max_runs,
        "auto_pause_after_failures": trigger.auto_pause_after_failures,
    }


async def _record_trigger_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    trigger: AgentTrigger,
    outcome: str = "success",
    reason_code: str | None = None,
    reason_message: str | None = None,
    run_id: str | uuid.UUID | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action=action,
        target_type="trigger",
        target_id=trigger.id,
        target_name_snapshot=trigger.name,
        target_owner_user_id=user.id,
        outcome=outcome,
        reason_code=reason_code,
        reason_message=reason_message,
        request=request,
        run_id=run_id,
        metadata={**_trigger_metadata(trigger), **(metadata or {})},
    )


def _map_trigger_validation(exc: ValueError) -> None:
    if "identity_mode must be fixed" in str(exc):
        raise agent_identity_requires_fixed() from exc
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
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    trigger = await trigger_service.get_trigger(db, trigger_id, user.id)
    if not trigger:
        raise trigger_not_found()
    try:
        updated = await trigger_service.update_trigger(db, trigger, data)
    except ValueError as exc:
        _map_trigger_validation(exc)
    await _record_trigger_audit(
        db,
        user=user,
        request=request,
        action="trigger.update",
        trigger=updated,
        metadata={
            "changed_fields": sorted(data.model_fields_set - {"input_message"}),
            "input_message_changed": "input_message" in data.model_fields_set,
        },
    )
    await db.commit()
    return updated


@router.delete("/api/triggers/{trigger_id}", status_code=204)
async def delete_trigger_global(
    trigger_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    trigger = await trigger_service.get_trigger(db, trigger_id, user.id)
    if not trigger:
        raise trigger_not_found()
    await _record_trigger_audit(
        db,
        user=user,
        request=request,
        action="trigger.delete",
        trigger=trigger,
    )
    await trigger_service.delete_trigger(db, trigger)


@router.post("/api/triggers/{trigger_id}/run-now", response_model=TriggerRunResponse)
async def run_trigger_now(
    trigger_id: uuid.UUID,
    request: Request,
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
    await _record_trigger_audit(
        db,
        user=user,
        request=request,
        action="trigger.run_now",
        trigger=trigger,
        outcome="success" if run.status == "success" else "failure",
        reason_code=None if run.status == "success" else "trigger_run_failed",
        reason_message=run.error_message,
        run_id=run.id,
        metadata={"run_status": run.status, "source": run.source},
    )
    await db.commit()
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
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await trigger_service.get_owned_agent(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    try:
        trigger = await trigger_service.create_trigger(db, agent_id, user.id, data)
    except ValueError as exc:
        _map_trigger_validation(exc)
    await _record_trigger_audit(
        db,
        user=user,
        request=request,
        action="trigger.create",
        trigger=trigger,
    )
    await db.commit()
    return trigger


@router.put(
    "/api/agents/{agent_id}/triggers/{trigger_id}",
    response_model=TriggerResponse,
)
async def update_agent_trigger(
    agent_id: uuid.UUID,
    trigger_id: uuid.UUID,
    data: TriggerUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    trigger = await trigger_service.get_trigger(db, trigger_id, user.id)
    if not trigger or trigger.agent_id != agent_id:
        raise trigger_not_found()
    try:
        updated = await trigger_service.update_trigger(db, trigger, data)
    except ValueError as exc:
        _map_trigger_validation(exc)
    await _record_trigger_audit(
        db,
        user=user,
        request=request,
        action="trigger.update",
        trigger=updated,
        metadata={
            "changed_fields": sorted(data.model_fields_set - {"input_message"}),
            "input_message_changed": "input_message" in data.model_fields_set,
        },
    )
    await db.commit()
    return updated


@router.delete(
    "/api/agents/{agent_id}/triggers/{trigger_id}",
    status_code=204,
)
async def delete_agent_trigger(
    agent_id: uuid.UUID,
    trigger_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    trigger = await trigger_service.get_trigger(db, trigger_id, user.id)
    if not trigger or trigger.agent_id != agent_id:
        raise trigger_not_found()
    await _record_trigger_audit(
        db,
        user=user,
        request=request,
        action="trigger.delete",
        trigger=trigger,
    )
    await trigger_service.delete_trigger(db, trigger)
