"""Organization metadata updates preserve unknown settings and audit the mutation."""

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.org_control import OrgControlContext, OrgControlTarget, load_org_control
from app.schemas.org import OrganizationPatch, OrganizationSettings, OrganizationView
from app.services import audit_service
from app.services.orgs.versioning import bump_version, require_current_version, utc_timestamp


@dataclass(frozen=True, slots=True)
class OrgMutationRequest:
    db: AsyncSession
    request: Request
    expected_version: str | None


def organization_view(context: OrgControlContext) -> OrganizationView:
    organization = context.organization
    return OrganizationView.model_validate(
        {
            "id": organization.id,
            "tenant_id": organization.tenant_id,
            "name": organization.name,
            "slug": organization.slug,
            "status": organization.status,
            "is_default": organization.is_default,
            "settings": OrganizationSettings.model_validate(organization.settings),
            "updated_at": utc_timestamp(organization.updated_at),
            "permissions": context.permissions,
        }
    )


async def update_organization_info(
    mutation: OrgMutationRequest, context: OrgControlContext, payload: OrganizationPatch
) -> OrganizationView:
    context = await load_org_control(
        mutation.db, context.actor, OrgControlTarget(context.organization.id, "can_manage_policy")
    )
    organization = context.organization
    require_current_version(organization, mutation.expected_version)
    settings = dict(organization.settings)
    if payload.settings is not None:
        settings.update(payload.settings.update_values())
    name = payload.name if payload.name is not None else organization.name
    if name == organization.name and settings == organization.settings:
        return organization_view(context)
    changed_fields = []
    if name != organization.name:
        changed_fields.append("name")
    if settings != organization.settings:
        changed_fields.append("settings")
    organization.name, organization.settings = name, settings
    bump_version(organization)
    for changed_field in changed_fields:
        await record_info_event(mutation, context, changed_field)
    await mutation.db.commit()
    return organization_view(context)


async def record_info_event(
    mutation: OrgMutationRequest, context: OrgControlContext, changed_field: str
) -> None:
    organization = context.organization
    await audit_service.record_event(
        mutation.db,
        actor_type="user",
        actor_user_id=context.actor.id,
        actor_email_snapshot=context.actor.email,
        owner_user_id=context.actor.id,
        action=audit_actions.ORG_UPDATE
        if changed_field == "name"
        else audit_actions.ORG_POLICY_UPDATE,
        target_type="organization",
        target_id=organization.id,
        target_name_snapshot=organization.name,
        outcome="success",
        request=mutation.request,
        metadata={"changed_fields": [changed_field], "authz_epoch": organization.authz_epoch},
    )
