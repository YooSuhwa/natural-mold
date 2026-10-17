"""Read and save usage limits with narrowing (plan 8.7 "조정"; H-06).

A lower limit may not exceed the tightest higher one: organization ≤ company; user,
agent and the organization's ``default_member_limit`` ≤ min(organization, company).
Saving above it is ``422 limit_exceeds_parent`` (details: ``scope``, ``parent_scope``,
``parent_limit``). Lowering a higher limit keeps larger lower limits as they are (runs
still stop at the higher one), and the sum of lower limits may exceed the higher one —
views show it as ``allocated``. Saves live in ``limit_writes``.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.usage_limit_access import LimitTarget
from app.authz.usage_limit_queries import org_member
from app.models.agent import Agent
from app.models.organization import Organization, OrganizationMember, Tenant
from app.schemas.usage_limits import LimitScope, UsageLimitView
from app.tenancy.limit_capacity import active_run_count, member_count
from app.tenancy.limit_usage import (
    decimal_limit,
    int_limit,
    load_org,
    load_tenant,
    member_limit,
    period_of,
    spend_used,
)

COMPANY_ONLY = ("member_limit", "concurrent_run_limit", "request_rate_limit")


def _tightest(limits: list[tuple[LimitScope, Decimal | None]]) -> tuple[LimitScope, Decimal] | None:
    present: list[tuple[LimitScope, Decimal]] = [
        (scope, value) for scope, value in limits if value is not None
    ]
    return min(present, key=lambda item: item[1]) if present else None


async def rows(db: AsyncSession, target: LimitTarget) -> tuple[Tenant, Organization | None]:
    tenant = await load_tenant(db, target.tenant_id)
    if tenant is None:
        raise authorization_error("not_found")
    org = await load_org(db, target.org_id) if target.org_id else None
    return tenant, org


def parent_of(
    target: LimitTarget, tenant: Tenant, org: Organization | None
) -> tuple[LimitScope, Decimal] | None:
    company = decimal_limit(tenant.limits.get("monthly_spend_limit"))
    if target.scope == "tenant":
        return None
    if target.scope == "org":
        return _tightest([("tenant", company)])
    return _tightest([("org", org.monthly_spend_limit if org else None), ("tenant", company)])


def require_within(
    target: LimitTarget, value: Decimal | None, parent: tuple[LimitScope, Decimal] | None
) -> None:
    if value is None or parent is None or value <= parent[1]:
        return
    raise authorization_error(
        "limit_exceeds_parent",
        {"scope": target.scope, "parent_scope": parent[0], "parent_limit": float(parent[1])},
    )


async def _member(db: AsyncSession, org_id: UUID, user_id: UUID) -> OrganizationMember:
    member = await org_member(db, org_id, user_id)
    if member is None:
        raise authorization_error("not_found")
    return member


async def limit_view(db: AsyncSession, target: LimitTarget) -> UsageLimitView:
    tenant, org = await rows(db, target)
    period = period_of(tenant.settings)
    used = await spend_used(db, target.scope, target.scope_id, period, org_id=target.org_id)
    parent = parent_of(target, tenant, org)
    view = UsageLimitView(
        scope=target.scope,
        id=target.scope_id,
        org_id=target.org_id,
        used=used,
        resets_at=period.resets_at,
        parent_limit=parent[1] if parent and target.oversight else None,
    )
    if target.scope == "tenant":
        view.monthly_spend_limit = decimal_limit(tenant.limits.get("monthly_spend_limit"))
        view.allocated = await db.scalar(
            select(func.sum(Organization.monthly_spend_limit)).where(
                Organization.tenant_id == tenant.id, Organization.status != "archived"
            )
        )
        view.member_limit = int_limit(tenant.limits.get("member_limit"))
        view.members = await member_count(db, tenant.id)
        view.concurrent_run_limit = int_limit(tenant.limits.get("concurrent_run_limit"))
        view.active_runs = await active_run_count(db, tenant.id)
        view.request_rate_limit = int_limit(tenant.limits.get("request_rate_limit"))
    elif target.scope == "org" and org is not None:
        view.monthly_spend_limit = org.monthly_spend_limit
        view.default_member_limit = decimal_limit(org.settings.get("default_member_limit"))
        view.allocated = await db.scalar(
            select(func.sum(OrganizationMember.monthly_spend_limit)).where(
                OrganizationMember.org_id == org.id, OrganizationMember.status == "active"
            )
        )
    elif target.scope == "user" and org is not None:
        view.monthly_spend_limit = (await _member(db, org.id, target.scope_id)).monthly_spend_limit
        view.effective_limit = await member_limit(db, org, target.scope_id)
    else:
        view.monthly_spend_limit = await db.scalar(
            select(Agent.monthly_spend_limit).where(Agent.id == target.scope_id)
        )
    if view.effective_limit is None:
        view.effective_limit = view.monthly_spend_limit
    if view.effective_limit is not None:
        view.remaining = max(view.effective_limit - used, Decimal(0))
    return view


async def version_of(db: AsyncSession, target: LimitTarget) -> datetime:
    """The row whose ``updated_at`` versions this limit (ETag / If-Match).

    Company → the company row; organization and user (an organization policy) → the
    organization row; agent → the agent row.
    """
    tenant, org = await rows(db, target)
    if target.scope == "tenant":
        return tenant.updated_at
    if target.scope == "agent":
        stamp = await db.scalar(
            select(Agent.updated_at)
            .where(Agent.id == target.scope_id)
            .execution_options(populate_existing=True)
        )
        if stamp is None:
            raise authorization_error("not_found")
        return stamp
    if org is None:
        raise authorization_error("not_found")
    return org.updated_at


__all__ = ["COMPANY_ONLY", "limit_view", "parent_of", "require_within", "rows", "version_of"]
