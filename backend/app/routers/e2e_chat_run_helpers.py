from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import conversation_not_found
from app.schemas.conversation_run import ConversationRunResponse
from app.services import chat_service, conversation_run_service

router = APIRouter(tags=["e2e"])


class E2EConversationRunCreate(BaseModel):
    status: Literal["queued", "running", "interrupted"] = "queued"
    source: Literal["chat", "start", "edit", "regenerate", "resume"] = "chat"
    input_preview: str | None = None
    interrupt_id: str | None = None


class E2EConversationRunHeartbeatUpdate(BaseModel):
    heartbeat_age_seconds: int = 600


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _require_e2e_user(user: CurrentUser) -> None:
    if user.email != settings.e2e_user_email:
        raise conversation_not_found()


@router.post(
    "/api/e2e/conversations/{conversation_id}/runs",
    response_model=ConversationRunResponse,
)
async def create_e2e_conversation_run(
    conversation_id: uuid.UUID,
    data: E2EConversationRunCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    _require_e2e_user(user)
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if conv is None:
        raise conversation_not_found()
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conv.id,
        agent_id=conv.agent_id,
        user_id=user.id,
        source=data.source,
        input_preview=data.input_preview,
    )
    if data.status == "running":
        await conversation_run_service.transition_run(
            db,
            run,
            "running",
            worker_instance_id="e2e-helper",
        )
    elif data.status == "interrupted":
        await conversation_run_service.transition_run(
            db,
            run,
            "running",
            worker_instance_id="e2e-helper",
        )
        await conversation_run_service.transition_run(
            db,
            run,
            "interrupted",
            interrupt_id=data.interrupt_id or "e2e-interrupt",
        )
    run.heartbeat_at = run.heartbeat_at or _utc_now_naive()
    await db.commit()
    await db.refresh(run)
    return ConversationRunResponse.model_validate(run)


@router.patch(
    "/api/e2e/conversations/{conversation_id}/runs/{run_id}/heartbeat",
    response_model=ConversationRunResponse,
)
async def update_e2e_conversation_run_heartbeat(
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    data: E2EConversationRunHeartbeatUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    _require_e2e_user(user)
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if conv is None:
        raise conversation_not_found()
    run = await conversation_run_service.get_run_for_user(
        db,
        conversation_id=conversation_id,
        run_id=run_id,
        user_id=user.id,
    )
    if run is None:
        raise conversation_not_found()
    age_seconds = max(data.heartbeat_age_seconds, 0)
    run.heartbeat_at = _utc_now_naive() - timedelta(seconds=age_seconds)
    await db.commit()
    await db.refresh(run)
    return ConversationRunResponse.model_validate(run)


@router.post("/api/e2e/conversations/{conversation_id}/runs/stale-sweep")
async def sweep_e2e_stale_conversation_runs(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    _require_e2e_user(user)
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if conv is None:
        raise conversation_not_found()
    marked = await conversation_run_service.mark_stale_active_runs(
        db,
        stale_before=_utc_now_naive() - timedelta(seconds=settings.chat_run_stale_after_seconds),
        worker_instance_id="e2e-helper",
        include_workerless=True,
        # path 의 conversation 으로 스코프 — 병렬 E2E 테스트가 서로의 active run
        # 을 stale 처리하는 cross-test 간섭 방지.
        conversation_id=conv.id,
    )
    await db.commit()
    return {"marked": marked}
