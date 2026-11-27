"""Authorize each outbound research search and reserve its organization quota."""

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import active_memberships
from app.authz.tenant_org_write_locks import lock_tenant_org_write
from app.database import tenant_session
from app.exceptions import AppError
from app.models.organization import Organization, Tenant
from app.models.research_search_usage import ResearchSearchUsage
from app.models.user import User
from app.schemas.research_search import (
    RESEARCH_SEARCH_POLICY_KEY,
    ResearchSearchPolicy,
    SearchProvider,
)
from app.tenancy.limit_usage import Subject, limit_states, period_of

ResearchSearchActor = Subject
Provider = SearchProvider


class SearchPolicyError(ValueError):
    """Terminal failure with a public message that carries no provider configuration."""


async def _policy(
    db: AsyncSession, actor: ResearchSearchActor, *, lock: bool
) -> tuple[Tenant, ResearchSearchPolicy | None]:
    if lock:
        # Membership mutations lock these parents first. Read membership in a
        # separate statement so a waiting transaction sees the committed change.
        try:
            await lock_tenant_org_write(db, actor.tenant_id, (actor.org_id,))
        except AppError as error:
            if error.status != 404:
                raise
            raise SearchPolicyError(
                "Research search is not authorized for this organization"
            ) from None
    query = (
        active_memberships(actor.user_id)
        .join(User, User.id == actor.user_id)
        .where(
            User.is_active.is_(True),
            Organization.id == actor.org_id,
            Organization.status == "active",
            Tenant.id == actor.tenant_id,
            Tenant.status == "active",
        )
    )
    row = (await db.execute(query)).one_or_none()
    if row is None:
        raise SearchPolicyError("Research search is not authorized for this organization")
    org, tenant = row
    if RESEARCH_SEARCH_POLICY_KEY not in org.settings:
        return tenant, None
    try:
        policy = ResearchSearchPolicy.model_validate(org.settings[RESEARCH_SEARCH_POLICY_KEY])
    except ValidationError:
        raise SearchPolicyError("Organization research search policy is invalid") from None
    return tenant, policy


async def load_plan(actor: ResearchSearchActor | None) -> tuple[Provider, ...]:
    """Read the provider order; reservations revalidate it before sending a query."""
    if actor is None:
        raise SearchPolicyError("Research search requires an authorized organization")
    async with tenant_session(actor) as db:
        _, policy = await _policy(db, actor, lock=False)
        if policy is None or not policy.providers:
            raise SearchPolicyError("Organization research search providers are not enabled")
        return policy.providers


async def reserve_attempt(
    actor: ResearchSearchActor | None, provider: Provider, *, legacy_tavily: bool = False
) -> str | None:
    """Commit one authorized attempt before HTTP, retaining the charge on unknown outcomes.

    The previously published Tavily tool keeps its operator-hosted default only for
    organizations that have never set a research search policy.
    """
    if actor is None:
        raise SearchPolicyError("Research search requires an authorized organization")
    async with tenant_session(actor) as db, db.begin():
        tenant, policy = await _policy(db, actor, lock=True)
        if policy is None:
            if not legacy_tavily or provider != "tavily":
                raise SearchPolicyError("Organization research search providers are not enabled")
        elif provider not in policy.providers:
            raise SearchPolicyError("Research search provider is not permitted")
        # Read booked spend directly on each attempt, including a fallback. The
        # model guard's short-lived cached balance is insufficient for this gate.
        if any(state.exceeded for state in await limit_states(db, actor)):
            raise SearchPolicyError("Organization spend limit is exhausted")
        if policy is None:
            return None
        if policy.monthly_attempt_limit == 0:
            raise SearchPolicyError("Organization research search budget is exhausted")
        month_start = period_of(tenant.settings).start
        counter = await db.get(ResearchSearchUsage, (actor.tenant_id, actor.org_id, month_start))
        if counter is None:
            db.add(
                ResearchSearchUsage(
                    tenant_id=actor.tenant_id,
                    org_id=actor.org_id,
                    month_start=month_start,
                    attempts=1,
                )
            )
        elif counter.attempts >= policy.monthly_attempt_limit:
            raise SearchPolicyError("Organization research search budget is exhausted")
        else:
            counter.attempts += 1
        return policy.searxng_endpoint if provider == "searxng" else None
