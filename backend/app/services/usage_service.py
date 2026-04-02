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
    # Get totals
    query = (
        select(
            func.coalesce(func.sum(TokenUsage.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(TokenUsage.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(TokenUsage.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(TokenUsage.estimated_cost), Decimal("0")).label(
                "estimated_cost"
            ),
        )
        .join(Agent, TokenUsage.agent_id == Agent.id)
        .where(Agent.user_id == user_id)
    )
    result = await db.execute(query)
    totals = result.one()

    # Get per-agent breakdown
    by_agent_query = (
        select(
            TokenUsage.agent_id,
            Agent.name.label("agent_name"),
            func.coalesce(func.sum(TokenUsage.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(TokenUsage.estimated_cost), Decimal("0")).label(
                "estimated_cost"
            ),
        )
        .join(Agent, TokenUsage.agent_id == Agent.id)
        .where(Agent.user_id == user_id)
        .group_by(TokenUsage.agent_id, Agent.name)
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
