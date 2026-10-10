"""Versioned catalog policy storage; fanout and runtime effects are attached in E-03."""

from dataclasses import dataclass

from app.authz import audit_actions
from app.authz.error_codes import authorization_error
from app.authz.org_catalog_queries import CatalogEntry, catalog_entries, company_catalog_settings
from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.schemas.org_catalog import CatalogItemView, CatalogPolicy, CatalogPolicyView
from app.services import audit_service
from app.services.orgs.info import OrgMutationRequest
from app.services.orgs.versioning import bump_version, require_current_version, utc_timestamp


@dataclass(frozen=True, slots=True)
class CatalogState:
    inventory: tuple[CatalogEntry, ...]
    company_policy: CatalogPolicy
    organization_policy: CatalogPolicy


async def load_catalog(mutation: OrgMutationRequest, context: OrgControlContext) -> CatalogState:
    company = await company_catalog_settings(mutation.db, context.organization.tenant_id)
    return CatalogState(
        inventory=await catalog_entries(mutation.db, company.id),
        company_policy=CatalogPolicy.model_validate(company.settings.get("catalog_policy", {})),
        organization_policy=CatalogPolicy.model_validate(
            context.organization.settings.get("catalog_policy", {})
        ),
    )


def catalog_view(context: OrgControlContext, state: CatalogState) -> CatalogPolicyView:
    items: list[CatalogItemView] = []
    for entry in state.inventory:
        company_enabled = entry.available and state.company_policy.enabled_for(entry.kind, entry.id)
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


async def update_catalog_policy(
    mutation: OrgMutationRequest, context: OrgControlContext, payload: CatalogPolicy
) -> CatalogPolicyView:
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
        if item.enabled and (
            not entry.available or not state.company_policy.enabled_for(item.type, item.id)
        ):
            raise authorization_error("catalog_policy_locked")
    canonical = CatalogPolicy(
        items=tuple(sorted(payload.items, key=lambda row: (row.type, row.id)))
    )
    previous = CatalogPolicy(
        items=tuple(sorted(state.organization_policy.items, key=lambda row: (row.type, row.id)))
    )
    if canonical == previous:
        return catalog_view(context, state)
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
    return catalog_view(context, CatalogState(state.inventory, state.company_policy, canonical))
