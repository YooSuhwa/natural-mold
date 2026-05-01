from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.token_usage import TokenUsage


async def get_agent_usage(
    db: AsyncSession,
    agent_id: uuid.UUID,
    period: str | None = None,
) -> dict:
    query = select(
        func.coalesce(func.sum(TokenUsage.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(TokenUsage.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(TokenUsage.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(TokenUsage.estimated_cost), Decimal("0")).label("estimated_cost"),
    ).where(TokenUsage.agent_id == agent_id)

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
        func.coalesce(func.sum(DailySpendUser.total_tokens_out), 0).label(
            "completion_tokens"
        ),
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

    # Per-agent breakdown — same source as totals so the cards match the
    # daily chart's per-agent slice.
    from app.models.daily_spend_agent import DailySpendAgent

    by_agent_query = (
        select(
            DailySpendAgent.agent_id,
            Agent.name.label("agent_name"),
            func.coalesce(
                func.sum(
                    DailySpendAgent.total_tokens_in
                    + DailySpendAgent.total_tokens_out
                ),
                0,
            ).label("total_tokens"),
            func.coalesce(
                func.sum(DailySpendAgent.total_cost_usd), Decimal("0")
            ).label("estimated_cost"),
        )
        .join(Agent, DailySpendAgent.agent_id == Agent.id)
        .where(Agent.user_id == user_id)
        .group_by(DailySpendAgent.agent_id, Agent.name)
    )
    by_agent_result = await db.execute(by_agent_query)

    by_agent = [
        {
            "agent_id": str(row.agent_id),
            "agent_name": row.agent_name,
            "total_tokens": row.total_tokens,
            "estimated_cost": float(row.estimated_cost),
        }
        for row in by_agent_result.all()
    ]

    return {
        "period": period or "all",
        "prompt_tokens": totals.prompt_tokens,
        "completion_tokens": totals.completion_tokens,
        "total_tokens": totals.total_tokens,
        "estimated_cost_usd": float(totals.estimated_cost),
        "by_agent": by_agent,
    }
