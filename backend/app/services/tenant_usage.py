"""Company usage by organization and a member's own remaining limit (plan 8.7, S26; H-06).

``tenant_usage`` reads ``daily_spend_user`` pinned to ``tenant_id`` (the billed caller's
organization rows), grouped per organization. ``my_usage_limit`` answers
``/api/me/usage-limits``: the caller's limit in the current organization, its use this
month and what is left under every higher limit — never the higher amounts themselves.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.daily_spend_user import DailySpendUser
from app.models.organization import Organization
from app.models.token_usage import TokenUsage
from app.schemas.org_usage import UsageTotals
from app.schemas.usage_limits import MyUsageLimit, TenantOrgUsage, TenantUsageSummary
from app.tenancy.limit_usage import (
    Subject,
    limit_states,
    load_org,
    load_tenant,
    member_limit,
    period_of,
    spend_used,
)


def _sums() -> list[Any]:
    return [
        func.coalesce(func.sum(DailySpendUser.total_tokens_in), 0).label("tokens_in"),
        func.coalesce(func.sum(DailySpendUser.total_tokens_out), 0).label("tokens_out"),
        func.coalesce(func.sum(DailySpendUser.total_cost_usd), Decimal("0")).label("cost"),
        func.coalesce(func.sum(DailySpendUser.request_count), 0).label("requests"),
    ]


def _totals(row: Any) -> dict[str, Any]:
    return {
        "total_tokens_in": int(getattr(row, "tokens_in", 0)),
        "total_tokens_out": int(getattr(row, "tokens_out", 0)),
        "total_cost_usd": float(getattr(row, "cost", 0)),
        "request_count": int(getattr(row, "requests", 0)),
    }


async def tenant_usage(
    db: AsyncSession, tenant_id: UUID, from_date: date, to_date: date
) -> TenantUsageSummary:
    window = (
        DailySpendUser.tenant_id == tenant_id,
        DailySpendUser.date >= from_date,
        DailySpendUser.date <= to_date,
    )
    total = (await db.execute(select(*_sums()).where(*window))).one()
    by_org = {
        row.org_id: row
        for row in (
            await db.execute(
                select(DailySpendUser.org_id, *_sums())
                .where(*window)
                .group_by(DailySpendUser.org_id)
            )
        ).all()
    }
    company_keys = (
        await db.execute(
            select(
                func.coalesce(func.sum(TokenUsage.prompt_tokens), 0).label("tokens_in"),
                func.coalesce(func.sum(TokenUsage.completion_tokens), 0).label("tokens_out"),
                func.coalesce(func.sum(TokenUsage.estimated_cost), Decimal("0")).label("cost"),
                func.count(TokenUsage.id).label("requests"),
            ).where(
                TokenUsage.tenant_id == tenant_id,
                TokenUsage.llm_credential_scope == "tenant",
                TokenUsage.created_at >= datetime.combine(from_date, time.min),
                TokenUsage.created_at < datetime.combine(to_date + timedelta(days=1), time.min),
            )
        )
    ).one()
    orgs = (await db.scalars(select(Organization).where(Organization.tenant_id == tenant_id))).all()
    items = [
        TenantOrgUsage(
            org_id=org.id,
            name=org.name,
            monthly_spend_limit=org.monthly_spend_limit,
            **_totals(by_org.get(org.id)),
        )
        for org in orgs
    ]
    items.sort(key=lambda item: (-item.total_cost_usd, item.name, str(item.org_id)))
    return TenantUsageSummary(
        tenant_id=tenant_id,
        from_date=from_date,
        to_date=to_date,
        organizations=items,
        company_keys=UsageTotals(**_totals(company_keys)),
        **_totals(total),
    )


async def my_usage_limit(db: AsyncSession, context: OrgContext) -> MyUsageLimit:
    tenant = await load_tenant(db, context.tenant_id)
    org = await load_org(db, context.org_id)
    period = period_of(tenant.settings if tenant else None)
    limit = await member_limit(db, org, context.user_id) if org else None
    used = await spend_used(db, "user", context.user_id, period, org_id=context.org_id)
    subject = Subject(context.tenant_id, context.org_id, context.user_id)
    states = [state for state in await limit_states(db, subject) if state.scope != "agent"]
    remaining = [state.remaining for state in states if state.remaining is not None]
    blocked = next((state for state in states if state.exceeded), None)
    return MyUsageLimit(
        org_id=context.org_id,
        resets_at=period.resets_at,
        limit=limit,
        used=used,
        remaining=min(remaining) if remaining else None,
        blocked=blocked is not None,
        blocked_scope=blocked.scope if blocked else None,
    )
