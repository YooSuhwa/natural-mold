from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.token_usage import TokenUsage
from app.services.usage_caller_breakdown import own_usage_by_agent


async def get_agent_usage(
    db: AsyncSession,
    agent_id: uuid.UUID,
    period: str | None = None,
    *,
    caller_user_id: uuid.UUID | None = None,
) -> dict:
    """``caller_user_id`` set: only that caller's usage (a runner's own, F-14)."""
    query = select(
        func.coalesce(func.sum(TokenUsage.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(TokenUsage.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(TokenUsage.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(TokenUsage.estimated_cost), Decimal("0")).label("estimated_cost"),
    ).where(TokenUsage.agent_id == agent_id)
    if caller_user_id is not None:
        query = query.where(TokenUsage.caller_user_id == caller_user_id)

    result = await db.execute(query)
    row = result.one()

    return {
        "agent_id": str(agent_id),
        "period": period or "all",
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "total_tokens": row.total_tokens,
        "estimated_cost_usd": float(row.estimated_cost),
    }


async def get_usage_summary(
    db: AsyncSession,
    user_id: uuid.UUID,
    period: str | None = None,
) -> dict:
    # Totals come from ``daily_spend_user`` so summary numbers match the daily
    # chart/table. The legacy ``token_usages`` rows are still written but the
    # spend writer (m21) is now the single source of truth for aggregate views.
    from app.models.daily_spend_user import DailySpendUser

    spend_query = select(
        func.coalesce(func.sum(DailySpendUser.total_tokens_in), 0).label("prompt_tokens"),
        func.coalesce(func.sum(DailySpendUser.total_tokens_out), 0).label("completion_tokens"),
        func.coalesce(
            func.sum(DailySpendUser.total_tokens_in + DailySpendUser.total_tokens_out),
            0,
        ).label("total_tokens"),
        func.coalesce(func.sum(DailySpendUser.total_cost_usd), Decimal("0")).label(
            "estimated_cost"
        ),
    ).where(DailySpendUser.user_id == user_id)
    spend_result = await db.execute(spend_query)
    totals = spend_result.one()

    # F-14: the breakdown is the user's own billed usage per agent (owned or shared), so
    # it adds up to the totals above and never shows other callers' cost on owned agents.
    by_agent = await own_usage_by_agent(db, user_id)

    return {
        "period": period or "all",
        "prompt_tokens": totals.prompt_tokens,
        "completion_tokens": totals.completion_tokens,
        "total_tokens": totals.total_tokens,
        "estimated_cost_usd": float(totals.estimated_cost),
        "by_agent": by_agent,
    }
