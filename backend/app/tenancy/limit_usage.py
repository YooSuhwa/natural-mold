"""Monthly spend of the four limit scopes (plan 8.7, D25, Q8; H-06).

Spend is read from the F-14 org-keyed daily aggregates: company and organization totals
and the per-organization user total come from ``daily_spend_user`` (the billed caller),
the agent total from ``daily_spend_agent``. The month starts on the 1st at 00:00 in the
company's time zone (``tenants.settings.timezone``, default Asia/Seoul). The aggregate
rows are whole UTC days, so a period is counted as the UTC days from the first UTC
midnight at or after the local month start up to the same point of the next month, and
``resets_at`` is exactly that next UTC midnight (Seoul: 09:00 KST on the 1st; New York:
the 2nd 00:00 UTC). Counted window and ``resets_at`` therefore always agree; the price is
a reset up to one day after local midnight (no partial boundary day is ever counted).

Stored limits (NULL = no limit, only the higher ones apply):

* company — ``tenants.limits.monthly_spend_limit`` (the B-12 key);
* organization — ``organizations.monthly_spend_limit``;
* user — ``organization_members.monthly_spend_limit`` of that organization, else the
  organization policy ``settings.default_member_limit``;
* agent — ``agents.monthly_spend_limit``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.usage_limit_queries import org_member
from app.models.agent import Agent
from app.models.daily_spend_agent import DailySpendAgent
from app.models.daily_spend_user import DailySpendUser
from app.models.organization import Organization, Tenant

Scope = Literal["tenant", "org", "user", "agent"]
SCOPES: Final[tuple[Scope, ...]] = ("tenant", "org", "user", "agent")
DEFAULT_TIMEZONE: Final = "Asia/Seoul"


@dataclass(frozen=True, slots=True)
class Subject:
    """Who a model call is booked to: the run's company, organization, caller and agent."""

    tenant_id: UUID
    org_id: UUID
    user_id: UUID
    agent_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Period:
    start: date  # first counted UTC day
    end: date  # first UTC day of the next period (exclusive)
    resets_at: datetime
    key: str  # the local month, ``YYYY-MM``


@dataclass(frozen=True, slots=True)
class LimitState:
    scope: Scope
    scope_id: UUID
    limit: Decimal | None
    used: Decimal
    period: Period

    @property
    def exceeded(self) -> bool:
        return self.limit is not None and self.used >= self.limit

    @property
    def remaining(self) -> Decimal | None:
        return None if self.limit is None else max(self.limit - self.used, Decimal(0))


def _zone(tenant_settings: Mapping[str, object] | None) -> ZoneInfo:
    name = (tenant_settings or {}).get("timezone")
    try:
        return ZoneInfo(name if isinstance(name, str) and name else DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def _first_of_next(first: date) -> date:
    return (first + timedelta(days=32)).replace(day=1)


def _boundary(first: date, zone: ZoneInfo) -> date:
    """The first UTC day counted for the local month starting on ``first``."""
    moment = datetime(first.year, first.month, 1, tzinfo=zone).astimezone(UTC)
    return moment.date() if moment.time() == time(0) else moment.date() + timedelta(days=1)


def period_of(tenant_settings: Mapping[str, object] | None, now: datetime | None = None) -> Period:
    zone = _zone(tenant_settings)
    moment = now or datetime.now(UTC)
    first = moment.astimezone(zone).date().replace(day=1)
    if moment.astimezone(UTC).date() < _boundary(first, zone):
        first = (first - timedelta(days=1)).replace(day=1)  # this month's count not started
    start, end = _boundary(first, zone), _boundary(_first_of_next(first), zone)
    resets = datetime(end.year, end.month, end.day, tzinfo=UTC)
    return Period(start, end, resets, f"{first:%Y-%m}")


def decimal_limit(value: object) -> Decimal | None:
    """A stored JSON limit (number or numeric string) as a Decimal; anything else = none."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def int_limit(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number >= 0 else None


async def spend_used(
    db: AsyncSession, scope: Scope, scope_id: UUID, period: Period, *, org_id: UUID | None = None
) -> Decimal:
    if scope == "agent":
        query = select(func.coalesce(func.sum(DailySpendAgent.total_cost_usd), 0)).where(
            DailySpendAgent.agent_id == scope_id,
            DailySpendAgent.date >= period.start,
            DailySpendAgent.date < period.end,
        )
    else:
        column = {
            "tenant": DailySpendUser.tenant_id,
            "org": DailySpendUser.org_id,
            "user": DailySpendUser.user_id,
        }[scope]
        query = select(func.coalesce(func.sum(DailySpendUser.total_cost_usd), 0)).where(
            column == scope_id,
            DailySpendUser.date >= period.start,
            DailySpendUser.date < period.end,
        )
        if scope == "user":
            query = query.where(DailySpendUser.org_id == org_id)
    return Decimal(str(await db.scalar(query) or 0))


async def load_tenant(db: AsyncSession, tenant_id: UUID) -> Tenant | None:
    return await db.scalar(
        select(Tenant).where(Tenant.id == tenant_id).execution_options(populate_existing=True)
    )


async def load_org(db: AsyncSession, org_id: UUID) -> Organization | None:
    return await db.scalar(
        select(Organization)
        .where(Organization.id == org_id)
        .execution_options(populate_existing=True)
    )


async def member_limit(db: AsyncSession, org: Organization, user_id: UUID) -> Decimal | None:
    """The user's limit in ``org``: their own value, else the organization default."""
    member = await org_member(db, org.id, user_id)
    own = member.monthly_spend_limit if member else None
    return own if own is not None else decimal_limit(org.settings.get("default_member_limit"))


async def configured_limits(
    db: AsyncSession, subject: Subject
) -> tuple[Period, list[tuple[Scope, UUID, Decimal]]]:
    """The period and every limit that applies to ``subject`` (unset scopes left out)."""
    tenant = await load_tenant(db, subject.tenant_id)
    org = await load_org(db, subject.org_id)
    period = period_of(tenant.settings if tenant else None)
    found: list[tuple[Scope, UUID, Decimal | None]] = [
        ("tenant", subject.tenant_id, decimal_limit(tenant.limits.get("monthly_spend_limit")))
        if tenant
        else ("tenant", subject.tenant_id, None)
    ]
    if org is not None:
        found.append(("org", org.id, org.monthly_spend_limit))
        found.append(("user", subject.user_id, await member_limit(db, org, subject.user_id)))
    if subject.agent_id is not None:
        agent_limit = await db.scalar(
            select(Agent.monthly_spend_limit).where(Agent.id == subject.agent_id)
        )
        found.append(("agent", subject.agent_id, agent_limit))
    return period, [(scope, sid, limit) for scope, sid, limit in found if limit is not None]


async def limit_states(db: AsyncSession, subject: Subject) -> tuple[LimitState, ...]:
    """Used vs limit of every configured scope, company first (no limit → no spend query)."""
    period, limits = await configured_limits(db, subject)
    return tuple(
        [
            LimitState(
                scope,
                sid,
                limit,
                await spend_used(db, scope, sid, period, org_id=subject.org_id),
                period,
            )
            for scope, sid, limit in limits
        ]
    )
