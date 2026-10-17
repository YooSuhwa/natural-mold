"""Organization usage views (plan 9.6 ``can_view_usage``, S26; H-03)."""

from datetime import date
from uuid import UUID

from pydantic import BaseModel


class UsageTotals(BaseModel):
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_usd: float = 0.0
    request_count: int = 0


class OrgUsageSummary(UsageTotals):
    org_id: UUID
    from_date: date
    to_date: date


class OrgMemberUsage(UsageTotals):
    user_id: UUID
    display_name: str


class OrgAgentUsage(UsageTotals):
    agent_id: UUID
    agent_name: str


class OrgMemberUsagePage(BaseModel):
    org_id: UUID
    from_date: date
    to_date: date
    items: list[OrgMemberUsage]


class OrgAgentUsagePage(BaseModel):
    org_id: UUID
    from_date: date
    to_date: date
    items: list[OrgAgentUsage]
