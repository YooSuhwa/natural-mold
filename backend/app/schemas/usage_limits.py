"""Usage limit views and writes (plan 8.7, 9.7 ``/api/usage-limits``, S08·S10·S26; H-06)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.org_usage import UsageTotals

LimitScope = Literal["tenant", "org", "user", "agent"]


class UsageLimitView(BaseModel):
    """One scope's monthly spend limit and this month's use.

    ``monthly_spend_limit`` is the stored value of the scope; ``effective_limit`` adds the
    organization default for a user without their own value. ``parent_limit`` is the
    tightest higher limit (the cap for a save), ``allocated`` the sum of the limits set
    one tier below (company: organizations; organization: members).
    """

    scope: LimitScope
    id: UUID
    org_id: UUID | None = None
    monthly_spend_limit: Decimal | None = None
    effective_limit: Decimal | None = None
    used: Decimal
    remaining: Decimal | None = None
    resets_at: datetime
    parent_limit: Decimal | None = None
    allocated: Decimal | None = None
    default_member_limit: Decimal | None = None
    member_limit: int | None = None
    members: int | None = None
    concurrent_run_limit: int | None = None
    active_runs: int | None = None
    request_rate_limit: int | None = None


class UsageLimitUpdate(BaseModel):
    """A full replacement of the scope's limits; omitted fields keep their value.

    ``member_limit``, ``concurrent_run_limit`` and ``request_rate_limit`` are company-only
    and ``default_member_limit`` organization-only (422 elsewhere).
    """

    model_config = ConfigDict(extra="forbid")

    monthly_spend_limit: Decimal | None = Field(default=None, ge=0, max_digits=20, decimal_places=8)
    default_member_limit: Decimal | None = Field(
        default=None, ge=0, max_digits=20, decimal_places=8
    )
    member_limit: int | None = Field(default=None, ge=0)
    concurrent_run_limit: int | None = Field(default=None, ge=0)
    request_rate_limit: int | None = Field(default=None, ge=0)


class MyUsageLimit(BaseModel):
    """What a member sees on ``/settings/usage``: their own limit and what is left."""

    org_id: UUID
    resets_at: datetime
    limit: Decimal | None = None
    used: Decimal
    remaining: Decimal | None = None
    blocked: bool = False
    blocked_scope: LimitScope | None = None


class TenantOrgUsage(UsageTotals):
    org_id: UUID
    name: str
    monthly_spend_limit: Decimal | None = None


class TenantUsageSummary(UsageTotals):
    tenant_id: UUID
    from_date: date
    to_date: date
    organizations: list[TenantOrgUsage]
