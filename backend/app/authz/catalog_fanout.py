"""Catalog policy -> ``system_fanout`` grants (plan 7.2, 7.2.1, 10.3, S09; E-03).

Every catalog item an organization may use (platform visibility, then company policy, then
organization policy) holds one active ``system_fanout`` grant per relation below whose
principal is the organization itself:

* system tool, skill, MCP server: ``allowed_org`` (``in_boundary``) and ``consumer``
  (``organization:O#member``), so ``can_use = consumer and in_boundary`` holds for members;
* model: ``consumer`` (``llm_model#can_use = consumer``).

Turning an item off revokes those grants through ``GrantService`` (8.6 marker path), so
once the deletes land no member's ``can_use`` holds; turning it on grants them again.
Request paths write nothing in legacy mode (8.4, same as the D-05 marketplace sync);
``python -m app.authz.cli catalog-fanout`` backfills organizations before shadow/enforce
and after new platform resources are seeded.

E-07 adds the company tier (``company_catalog.CompanyTier``: company policy,
``allow_system_models``, model designation) and company shared credentials designated to
an organization (``credential``: ``allowed_org`` + ``consumer``), so one per-organization
set covers both and the backfill projects them together.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.company_catalog import POLICY_KEY, CompanyTier, company_tier, stored_policy
from app.authz.grant_projection import lock_object
from app.authz.grants import GrantRequest, GrantService, GrantTarget
from app.authz.org_catalog_queries import (
    CatalogEntry,
    catalog_entries,
    company_catalog_settings,
    company_credential_ids,
)
from app.authz.org_context import OrgContext
from app.authz.resource_lifecycle import LifecycleChange
from app.authz.service_types import AuthzMode
from app.models.authz import ResourceGrant
from app.models.organization import Organization
from app.schemas.org_catalog import CatalogKind, CatalogPolicy

__all__ = ["POLICY_KEY", "CompanyTier", "stored_policy"]

GRANT_KIND = "system_fanout"
type FanoutKind = CatalogKind | Literal["credential"]
FGA_TYPES: dict[FanoutKind, str] = {
    "tool": "tool",
    "skill": "skill",
    "mcp_server": "mcp_server",
    "model": "llm_model",
    "credential": "credential",
}
_BOUNDED = ("allowed_org", "consumer")
RELATIONS: dict[FanoutKind, tuple[str, ...]] = {
    "tool": _BOUNDED,
    "skill": _BOUNDED,
    "mcp_server": _BOUNDED,
    "model": ("consumer",),
    "credential": _BOUNDED,
}

type FanoutKey = tuple[str, UUID, str]  # (resource_type, resource_id, relation)


def company_allows(company: CompanyTier, entry: CatalogEntry, org_id: UUID) -> bool:
    """Platform and company tiers both keep the item for ``org_id``."""
    return company.offers(
        entry.kind, entry.id, entry.source, available=entry.available
    ) and company.reaches(entry.kind, entry.id, entry.source, org_id)


def enabled_entries(
    inventory: Iterable[CatalogEntry],
    company: CompanyTier,
    organization: CatalogPolicy,
    org_id: UUID,
) -> tuple[CatalogEntry, ...]:
    """Items the organization may use: platform → company → organization, each narrowing."""
    return tuple(
        entry
        for entry in inventory
        if company_allows(company, entry, org_id) and organization.enabled_for(entry.kind, entry.id)
    )


def credential_fanout(
    credentials: Iterable[UUID], company: CompanyTier, org_id: UUID
) -> frozenset[FanoutKey]:
    """Company shared credentials designated to ``org_id`` (7.2 회사 공용 자격증명)."""
    return frozenset(
        ("credential", credential, relation)
        for credential in credentials
        if company.credential_reaches(credential, org_id)
        for relation in RELATIONS["credential"]
    )


def expected_fanout(entries: Iterable[CatalogEntry]) -> frozenset[FanoutKey]:
    return frozenset(
        (FGA_TYPES[entry.kind], entry.id, relation)
        for entry in entries
        for relation in RELATIONS[entry.kind]
    )


async def active_fanout(db: AsyncSession, org_id: UUID) -> dict[FanoutKey, list[ResourceGrant]]:
    found = await db.scalars(
        select(ResourceGrant)
        .where(
            ResourceGrant.org_id == org_id,
            ResourceGrant.grant_kind == GRANT_KIND,
            ResourceGrant.principal_type == "organization",
            ResourceGrant.principal_id == org_id,
            ResourceGrant.state == "active",
            ResourceGrant.resource_type.in_(tuple(FGA_TYPES.values())),
        )
        .order_by(ResourceGrant.id)
    )
    keyed: dict[FanoutKey, list[ResourceGrant]] = {}
    for grant in found:
        key = (grant.resource_type, grant.resource_id, grant.relation)
        keyed.setdefault(key, []).append(grant)
    return keyed


async def sync_fanout(
    db: AsyncSession,
    ctx: OrgContext,
    want: frozenset[FanoutKey],
    *,
    only: frozenset[tuple[str, UUID]] | None = None,
    service: GrantService | None = None,
) -> LifecycleChange:
    """Make the organization's active fanout grants equal ``want`` (before commit; the
    caller holds the organization row); ``apply_lifecycle`` the result after commit.

    ``only`` limits the diff to those objects (one item across organizations). Every
    touched object is locked first in one global sorted order, then the changes run in a
    single sorted pass, so concurrent syncs never wait on each other in opposite orders.
    """
    have = await active_fanout(db, ctx.org_id)
    if only is not None:
        have = {key: rows for key, rows in have.items() if key[:2] in only}
        want = frozenset(key for key in want if key[:2] in only)
    changed = sorted((have.keys() - want) | (want - have.keys()), key=str)
    for resource_type, resource_id in sorted({key[:2] for key in changed}, key=str):
        await lock_object(db, resource_type, resource_id, org_id=ctx.org_id)
    grants = service or GrantService()
    outbox: list[UUID] = []
    for key in changed:
        for stale in have.get(key, ()):
            outbox += (await grants.revoke(db, ctx, stale.id)).outbox_ids
        if key in want:
            resource_type, resource_id, relation = key
            request = GrantRequest(relation, "organization", ctx.org_id, grant_kind=GRANT_KIND)
            target = GrantTarget(resource_type, resource_id)
            outbox += (await grants.grant(db, ctx, target, request)).outbox_ids
    return LifecycleChange(tuple(outbox))


@dataclass(frozen=True, slots=True)
class CompanyState:
    tier: CompanyTier
    inventory: tuple[CatalogEntry, ...]
    credentials: tuple[UUID, ...]


async def company_state(db: AsyncSession, tenant_id: UUID, *, lock: bool = True) -> CompanyState:
    company = await company_catalog_settings(db, tenant_id, lock=lock)
    return CompanyState(
        company_tier(company),
        await catalog_entries(db, company.id),
        await company_credential_ids(db, company.id),
    )


def fanout_for(state: CompanyState, organization: Organization) -> frozenset[FanoutKey]:
    policy = stored_policy(organization.settings)
    entries = enabled_entries(state.inventory, state.tier, policy, organization.id)
    return expected_fanout(entries) | credential_fanout(
        state.credentials, state.tier, organization.id
    )


async def organization_fanout(db: AsyncSession, organization: Organization) -> frozenset[FanoutKey]:
    """The fanout the stored company and organization settings call for (locks the tenant)."""
    return fanout_for(await company_state(db, organization.tenant_id), organization)


async def sync_organization(
    db: AsyncSession,
    organization: Organization,
    actor_id: UUID,
    *,
    mode: AuthzMode,
    service: GrantService | None = None,
) -> LifecycleChange:
    """Request-path hook (organization creation): nothing in legacy mode."""
    if mode == "legacy":
        return LifecycleChange()
    await db.flush()
    ctx = OrgContext(org_id=organization.id, tenant_id=organization.tenant_id, user_id=actor_id)
    want = await organization_fanout(db, organization)
    return await sync_fanout(db, ctx, want, service=service)
