from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.services import usage_service

router = APIRouter(prefix="/api", tags=["usage"])


@router.get("/agents/{agent_id}/usage")
async def get_agent_usage(
    agent_id: uuid.UUID,
    period: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await usage_service.get_agent_usage(db, agent_id, period)


@router.get("/usage/summary")
async def get_usage_summary(
    period: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await usage_service.get_usage_summary(db, user.id, period)
