"""Company catalog policy ``GET|PUT /api/tenants/{id}/catalog-policy`` (9.7, 10.3; E-07).

The company can only turn platform-available items off (an item the platform hides or
disables cannot be enabled: ``catalog_policy_locked``); organizations then narrow further
(E-03). A change re-derives the fanout of every organization of the company.
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.company_catalog import POLICY_KEY, company_tier, stored_policy
from app.authz.error_codes import authorization_error
from app.authz.org_catalog_queries import CatalogEntry, catalog_entries
from app.models.organization import Tenant
from app.schemas.org_catalog import CatalogPolicy
from app.schemas.tenant_catalog import TenantCatalogItemView, TenantCatalogPolicyView
from app.services.tenants.catalog_common import (
    TenantCatalogMutation,
    commit_company_change,
    lock_for_write,
    record,
)
from app.services.tenants.versioning import bump_timestamp, require_current_version, utc_timestamp
from app.tenancy.admin_context import TenantAdminContext


def catalog_view(tenant: Tenant, inventory: tuple[CatalogEntry, ...]) -> TenantCatalogPolicyView:
    tier = company_tier(tenant)
    items = tuple(
        TenantCatalogItemView(
            type=entry.kind,
            id=entry.id,
            name=entry.name,
            source=entry.source,
            platform_available=entry.available,
            company_enabled=tier.offers(
                entry.kind, entry.id, entry.source, available=entry.available
            ),
        )
        for entry in inventory
    )
    return TenantCatalogPolicyView(
        items=items,
        allow_system_models=tier.allow_system_models,
        updated_at=utc_timestamp(tenant.updated_at),
    )


async def read_company_catalog(
    db: AsyncSession, context: TenantAdminContext
) -> TenantCatalogPolicyView:
    return catalog_view(context.tenant, await catalog_entries(db, context.tenant.id))


def _sorted(policy: CatalogPolicy) -> CatalogPolicy:
    return CatalogPolicy(items=tuple(sorted(policy.items, key=lambda row: (row.type, row.id))))


async def update_company_catalog(
    mutation: TenantCatalogMutation,
    tenant_id: UUID,
    payload: CatalogPolicy,
    expected_version: str | None,
) -> TenantCatalogPolicyView:
    tenant = (await lock_for_write(mutation, tenant_id)).tenant
    require_current_version(tenant.updated_at, expected_version)
    inventory = await catalog_entries(mutation.db, tenant.id)
    known = {(entry.kind, entry.id): entry for entry in inventory}
    for item in payload.items:
        entry = known.get((item.type, item.id))
        if entry is None:
            raise authorization_error("not_found")
        if item.enabled and not entry.available:  # the platform hid or disabled it
            raise authorization_error("catalog_policy_locked")
    canonical, previous = _sorted(payload), _sorted(stored_policy(tenant.settings))
    if canonical == previous:
        return catalog_view(tenant, inventory)
    tenant.settings = {**(tenant.settings or {}), POLICY_KEY: canonical.model_dump(mode="json")}
    tenant.updated_at = bump_timestamp(tenant.updated_at)
    await record(
        mutation,
        tenant,
        audit_actions.TENANT_CATALOG_UPDATE,
        ("tenant", tenant.id, tenant.name),
        {
            "change": "catalog_policy",
            "before": previous.model_dump(mode="json"),
            "after": canonical.model_dump(mode="json"),
        },
    )
    view = catalog_view(tenant, inventory)
    await commit_company_change(mutation, tenant, None, None)  # every item, every org
    return view
