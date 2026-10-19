"""Versioned catalog policy storage, its ``system_fanout`` grants (E-03) and impact preview.

A PUT that changes the policy re-syncs the organization's fanout grants in the same
transaction (``catalog_fanout.sync_fanout``; none in legacy mode) so turned-off items lose
every member's ``can_use`` once the outbox deletes land. Runtime exclusion of already-linked
items consumes that ``can_use`` (F-06); connection-summary invalidation is G-01.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.catalog_fanout import (
    company_allows,
    credential_fanout,
    enabled_entries,
    expected_fanout,
    stored_policy,
    sync_fanout,
)
from app.authz.company_catalog import CompanyTier, company_tier
from app.authz.error_codes import authorization_error
from app.authz.org_catalog_queries import (
    CatalogEntry,
    catalog_entries,
    catalog_impact,
    company_catalog_settings,
    company_credential_ids,
)
from app.authz.org_context import OrgContext
from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.authz.resource_lifecycle import LifecycleChange
from app.authz.service_types import AuthzMode
from app.schemas.org_catalog import (
    CatalogImpactView,
    CatalogItemView,
    CatalogKind,
    CatalogPolicy,
    CatalogPolicyView,
)
from app.services import audit_service
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.versioning import bump_version, require_current_version, utc_timestamp


@dataclass(frozen=True, slots=True)
class CatalogState:
    inventory: tuple[CatalogEntry, ...]
    company: CompanyTier
    organization_policy: CatalogPolicy


async def load_catalog(mutation: OrgMutationRequest, context: OrgControlContext) -> CatalogState:
    return await catalog_state(mutation.db, context)


async def catalog_state(
    db: AsyncSession, context: OrgControlContext, *, lock: bool = True
) -> CatalogState:
    company = await company_catalog_settings(db, context.organization.tenant_id, lock=lock)
    return CatalogState(
        inventory=await catalog_entries(db, company.id),
        company=company_tier(company),
        organization_policy=stored_policy(context.organization.settings),
    )


def catalog_view(context: OrgControlContext, state: CatalogState) -> CatalogPolicyView:
    items: list[CatalogItemView] = []
    for entry in state.inventory:
        company_enabled = company_allows(state.company, entry, context.organization.id)
        organization_enabled = state.organization_policy.enabled_for(entry.kind, entry.id)
        items.append(
            CatalogItemView(
                type=entry.kind,
                id=entry.id,
                name=entry.name,
                source=entry.source,
                company_enabled=company_enabled,
                organization_enabled=organization_enabled,
                enabled=company_enabled and organization_enabled,
            )
        )
    return CatalogPolicyView(
        items=tuple(items), updated_at=utc_timestamp(context.organization.updated_at)
    )


async def read_catalog_policy(
    mutation: OrgMutationRequest, context: OrgControlContext
) -> CatalogPolicyView:
    return catalog_view(context, await load_catalog(mutation, context))


async def catalog_policy_impact(
    db: AsyncSession, context: OrgControlContext, kind: CatalogKind, item_id: UUID
) -> CatalogImpactView:
    """Read-only preview: no organization or company row lock."""
    state = await catalog_state(db, context, lock=False)
    if all((entry.kind, entry.id) != (kind, item_id) for entry in state.inventory):
        raise authorization_error("not_found")
    affected, required = await catalog_impact(db, context.organization, kind, item_id)
    return CatalogImpactView(
        type=kind, id=item_id, affected_agents=affected, required_agents=required
    )


async def update_catalog_policy(
    mutation: OrgMutationRequest,
    context: OrgControlContext,
    payload: CatalogPolicy,
    *,
    mode: AuthzMode = "legacy",
) -> tuple[CatalogPolicyView, LifecycleChange]:
    """Store the overrides; outside legacy mode also queue the fanout grant changes
    (``apply_lifecycle`` the returned change after this commits)."""
    context = await load_org_control(
        mutation.db, context.actor, OrgControlTarget(context.organization.id, "can_manage_policy")
    )
    require_current_version(context.organization, mutation.expected_version)
    state = await load_catalog(mutation, context)
    inventory = {(entry.kind, entry.id): entry for entry in state.inventory}
    for item in payload.items:
        entry = inventory.get((item.type, item.id))
        if entry is None:
            raise authorization_error("not_found")
        if item.enabled and not company_allows(state.company, entry, context.organization.id):
            raise authorization_error("catalog_policy_locked")
    canonical = CatalogPolicy(
        items=tuple(sorted(payload.items, key=lambda row: (row.type, row.id)))
    )
    previous = CatalogPolicy(
        items=tuple(sorted(state.organization_policy.items, key=lambda row: (row.type, row.id)))
    )
    if canonical == previous:
        return catalog_view(context, state), LifecycleChange()
    change = LifecycleChange()
    if mode != "legacy":
        change = await _sync_fanout(mutation, context, state, canonical)
    context.organization.settings = {
        **context.organization.settings,
        "catalog_policy": canonical.model_dump(mode="json"),
    }
    bump_version(context.organization)
    await audit_service.record_event(
        mutation.db,
        actor_type="user",
        actor_user_id=context.actor.id,
        actor_email_snapshot=context.actor.email,
        owner_user_id=context.actor.id,
        action=audit_actions.ORG_CATALOG_POLICY_UPDATE,
        target_type="organization",
        target_id=context.organization.id,
        target_name_snapshot=context.organization.name,
        outcome="success",
        request=mutation.request,
        metadata={
            "before": previous.model_dump(mode="json"),
            "after": canonical.model_dump(mode="json"),
            "authz_epoch": context.organization.authz_epoch,
        },
    )
    await mutation.db.commit()
    return catalog_view(context, CatalogState(state.inventory, state.company, canonical)), (change)


async def _sync_fanout(
    mutation: OrgMutationRequest,
    context: OrgControlContext,
    state: CatalogState,
    policy: CatalogPolicy,
) -> LifecycleChange:
    """Fanout first: a revocation bumps the epoch with a SQL UPDATE (which also stamps
    ``updated_at``), so the row is re-read before ``bump_version`` sets the new ETag."""
    organization = context.organization
    actor = OrgContext(
        org_id=organization.id, tenant_id=organization.tenant_id, user_id=context.actor.id
    )
    entries = enabled_entries(state.inventory, state.company, policy, organization.id)
    credentials = await company_credential_ids(mutation.db, organization.tenant_id)
    want = expected_fanout(entries) | credential_fanout(credentials, state.company, organization.id)
    change = await sync_fanout(mutation.db, actor, want)
    await mutation.db.refresh(organization, ["updated_at", "authz_epoch"])
    return change
