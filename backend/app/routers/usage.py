from __future__ import annotations

import uuid
from datetime import date as date_type
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.services import usage_aggregate, usage_service

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


@router.get("/usage/daily")
async def get_daily_spend(
    target_kind: Literal["user", "agent", "model"] = Query(
        ..., description="Aggregate axis."
    ),
    target_id: uuid.UUID | None = Query(
        None, description="Optional single target id; omit for all targets."
    ),
    from_: date_type | None = Query(
        None, alias="from", description="Inclusive start date (default: today - 30d)."
    ),
    to: date_type | None = Query(
        None, description="Inclusive end date (default: today)."
    ),
    group_by: Literal["date", "target"] = Query(
        "date",
        description=(
            "``date`` returns one row per day. ``target`` returns one row per "
            "(date, target_id) pair so the dashboard can render multi-series."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Daily spend aggregates for the current user.

    Tenancy is enforced inside :func:`usage_aggregate.get_daily_spend` — the
    ``user`` axis filters on ``DailySpendUser.user_id`` directly while
    ``agent`` / ``model`` join through ``Agent.user_id`` so a user only ever
    sees rows they contributed to.
    """

    return await usage_aggregate.get_daily_spend(
        db,
        user_id=user.id,
        target_kind=target_kind,
        target_id=target_id,
        from_date=from_,
        to_date=to,
        group_by=group_by,
    )
