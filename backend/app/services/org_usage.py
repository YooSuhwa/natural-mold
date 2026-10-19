"""Organization usage from the F-14 org-keyed spend aggregates (plan 6.2, 8.7, S26; H-03).

Every query is pinned to ``org_id`` (``daily_spend_user``/``daily_spend_agent``), so no
other organization's rows can appear. Totals and the member table come from
``daily_spend_user`` (the billed requester); the agent table from ``daily_spend_agent``,
standard agents only (hidden builder/runtime agents are left out, as in ``/usage``).
The caller has already checked ``can_view_usage``.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.daily_spend_agent import DailySpendAgent
from app.models.daily_spend_user import DailySpendUser
from app.models.user import User
from app.schemas.org_usage import OrgAgentUsage, OrgMemberUsage, UsageTotals

type SpendTable = type[DailySpendUser] | type[DailySpendAgent]


def _sums(table: SpendTable) -> list[Any]:
    return [
        func.coalesce(func.sum(table.total_tokens_in), 0).label("tokens_in"),
        func.coalesce(func.sum(table.total_tokens_out), 0).label("tokens_out"),
        func.coalesce(func.sum(table.total_cost_usd), Decimal("0")).label("cost"),
        func.coalesce(func.sum(table.request_count), 0).label("requests"),
    ]


def _window(table: SpendTable, org_id: UUID, from_date: date, to_date: date) -> list[Any]:
    return [table.org_id == org_id, table.date >= from_date, table.date <= to_date]


def _totals(row: Any) -> dict[str, Any]:
    return {
        "total_tokens_in": int(row.tokens_in),
        "total_tokens_out": int(row.tokens_out),
        "total_cost_usd": float(row.cost),
        "request_count": int(row.requests),
    }


async def org_totals(db: AsyncSession, org_id: UUID, from_date: date, to_date: date) -> UsageTotals:
    query = select(*_sums(DailySpendUser)).where(
        *_window(DailySpendUser, org_id, from_date, to_date)
    )
    return UsageTotals(**_totals((await db.execute(query)).one()))


async def org_usage_by_member(
    db: AsyncSession, org_id: UUID, from_date: date, to_date: date
) -> list[OrgMemberUsage]:
    member = DailySpendUser.user_id.label("member_id")
    query = (
        select(member, *_sums(DailySpendUser))
        .where(*_window(DailySpendUser, org_id, from_date, to_date))
        .group_by(DailySpendUser.user_id)
    )
    rows = (await db.execute(query)).all()
    ids = [row.member_id for row in rows]
    names = {
        row.id: row.name or row.email
        for row in (
            await db.execute(select(User.id, User.name, User.email).where(User.id.in_(ids)))
        ).all()
    }
    items = [
        OrgMemberUsage(
            user_id=row.member_id,
            display_name=names.get(row.member_id) or str(row.member_id),
            **_totals(row),
        )
        for row in rows
    ]
    return sorted(items, key=lambda item: (-item.total_cost_usd, str(item.user_id)))


async def org_usage_by_agent(
    db: AsyncSession, org_id: UUID, from_date: date, to_date: date
) -> list[OrgAgentUsage]:
    query = (
        select(DailySpendAgent.agent_id, Agent.name, *_sums(DailySpendAgent))
        .join(Agent, Agent.id == DailySpendAgent.agent_id)
        .where(
            *_window(DailySpendAgent, org_id, from_date, to_date),
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
        )
        .group_by(DailySpendAgent.agent_id, Agent.name)
    )
    items = [
        OrgAgentUsage(agent_id=row.agent_id, agent_name=row.name, **_totals(row))
        for row in (await db.execute(query)).all()
    ]
    return sorted(items, key=lambda item: (-item.total_cost_usd, str(item.agent_id)))
