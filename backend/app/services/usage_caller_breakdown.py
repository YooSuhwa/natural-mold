"""A user's own spend broken down by agent / model (F-14, plan 8.7 Q8, 9.6).

The per-user total comes from ``daily_spend_user`` (the billed requester). Breakdowns
must add up to that and must not show other people's cost on the user's agents, so they
are read from ``token_usages`` rows the user was billed for (``caller_user_id``) — the
``daily_spend_agent``/``daily_spend_model`` aggregates have no user dimension. Agent-wide
totals (every caller) stay on ``GET /api/agents/{id}/usage`` (``can_view_config``).
Hidden runtime agents (``runtime_profile != 'standard'``) are left out, as before.
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Row, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.token_usage import TokenUsage

Axis = Literal["agent", "model"]


def _own_usage[*T](query: Select[tuple[*T]], user_id: uuid.UUID) -> Select[tuple[*T]]:
    return query.join(Agent, Agent.id == TokenUsage.agent_id).where(
        TokenUsage.caller_user_id == user_id,
        Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
    )


async def own_usage_by_agent(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """``/usage/summary`` ``by_agent``: the user's own usage per agent (owned or shared)."""
    query = _own_usage(
        select(
            TokenUsage.agent_id,
            Agent.name.label("agent_name"),
            func.coalesce(func.sum(TokenUsage.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(TokenUsage.estimated_cost), Decimal("0")).label("cost"),
        ),
        user_id,
    ).group_by(TokenUsage.agent_id, Agent.name)
    return [
        {
            "agent_id": str(row.agent_id),
            "agent_name": row.agent_name,
            "total_tokens": row.total_tokens,
            "estimated_cost": float(row.cost),
        }
        for row in (await db.execute(query)).all()
    ]


async def own_daily_rows(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    axis: Axis,
    target_id: uuid.UUID | None,
    from_date: date_type,
    to_date: date_type,
    by_target: bool,
) -> list[Row[Any]]:
    """``/usage/daily`` agent/model axes: rows labelled like the aggregate tables'."""
    day = func.date(TokenUsage.created_at)
    target = TokenUsage.agent_id if axis == "agent" else Agent.model_id
    columns: list[Any] = [
        day.label("date"),
        func.sum(TokenUsage.prompt_tokens).label("total_tokens_in"),
        func.sum(TokenUsage.completion_tokens).label("total_tokens_out"),
        func.sum(TokenUsage.estimated_cost).label("total_cost_usd"),
        func.count(TokenUsage.id).label("request_count"),
    ]
    groups: list[Any] = [day]
    if by_target:
        columns.append(target.label("target_id"))
        groups.append(target)
    query = _own_usage(select(*columns), user_id).where(
        TokenUsage.created_at >= datetime.combine(from_date, time.min),
        TokenUsage.created_at < datetime.combine(to_date + timedelta(days=1), time.min),
    )
    if target_id is not None:
        query = query.where(target == target_id)
    query = query.group_by(*groups).order_by(*(group.asc() for group in groups))
    return list((await db.execute(query)).all())


__all__ = ["own_daily_rows", "own_usage_by_agent"]
